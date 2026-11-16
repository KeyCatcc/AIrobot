"""Forward completed duplex transcripts to ARDY without blocking audio."""
import json
import os
import queue
import subprocess
import threading
import urllib.request
import uuid
from pathlib import Path


class ARDYBridge:
    def __init__(self):
        self.enabled = os.environ.get("ARDY_ENABLED") == "1"
        self.closed = threading.Event()
        self.pending = queue.Queue(maxsize=4)
        self.seen = set()
        self.resident_child = None
        if self.enabled:
            self.url = os.environ.get("ARDY_BASE_URL", "http://172.16.21.234:8765").rstrip("/")
            self.duration = float(os.environ.get("ARDY_DURATION", "2"))
            self.seed = int(os.environ.get("ARDY_SEED", "0"))
            self.timeout = float(os.environ.get("ARDY_TIMEOUT", "180"))
            if not 1 <= self.duration <= 30 or self.timeout <= 0:
                raise ValueError("ARDY_DURATION must be 1..30; ARDY_TIMEOUT must be positive")
            self.pipeline = os.environ.get("ARDY_PIPELINE") == "1"
            self.project_root = Path(__file__).resolve().parents[2]
            self.pipeline_script = self.project_root / "serverobot-v2/tools/ardy_smplx/run_action_task.py"
            self.stream = self.pipeline and os.environ.get('ARDY_STREAM') == '1'
            self.stream_script = self.pipeline_script.with_name('stream_action_task.py')
            default_python = self.project_root / "serverobot-v2/tools/ardy_smplx/.venv/Scripts/python.exe"
            self.pipeline_python = os.environ.get("ARDY_PIPELINE_PYTHON", str(default_python))
            selected_script = self.stream_script if self.stream else self.pipeline_script
            if self.pipeline and (not Path(self.pipeline_python).is_file() or not selected_script.is_file()):
                raise ValueError("ARDY pipeline Python or script not found")
            self.resident = self.pipeline and os.environ.get('ARDY_RESIDENT', '1') == '1'
            if self.stream and not self.resident:
                raise ValueError('ARDY_STREAM=1 requires ARDY_RESIDENT=1')
            if self.resident:
                self._start_resident_worker()
            self.output = Path(os.environ.get("ARDY_OUTPUT_DIR", "ardy_motions"))
            threading.Thread(target=self._worker, daemon=True).start()

    def submit(self, text, item_id=None):
        if not self.enabled or self.closed.is_set() or not isinstance(text, str) or not text.strip():
            return
        if item_id and item_id in self.seen:
            return
        try:
            self.pending.put_nowait(text)
        except queue.Full:
            print("[ARDY] queue full; instruction skipped", flush=True)
            return
        if item_id:
            self.seen.add(item_id)

    def _worker(self):
        while not self.closed.is_set():
            try:
                prompt = self.pending.get(timeout=0.2)
            except queue.Empty:
                continue
            if self.closed.is_set():
                return
            try:
                if self.pipeline:
                    self._run_pipeline(prompt)
                    continue
                request = urllib.request.Request(
                    self.url + "/generate",
                    data=json.dumps({"prompt": prompt, "duration": self.duration, "seed": self.seed}).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                self.output.mkdir(parents=True, exist_ok=True)
                target = self.output / (uuid.uuid4().hex + ".npz")
                partial = target.with_suffix(".part")
                try:
                    with urllib.request.urlopen(request, timeout=self.timeout) as response:
                        with partial.open("wb") as stream:
                            while True:
                                chunk = response.read(65536)
                                if not chunk:
                                    break
                                stream.write(chunk)
                    partial.replace(target)
                finally:
                    if partial.exists():
                        partial.unlink()
                print("[ARDY] motion saved: " + str(target.resolve()), flush=True)
            except Exception as exc:
                print("[ARDY] generation failed: " + str(exc), flush=True)

    def _run_pipeline(self, prompt):
        print("[ARDY pipeline] processing instruction: " + prompt, flush=True)
        script = self.stream_script if self.stream else self.pipeline_script
        command = [self.pipeline_python, str(script), "--prompt", prompt,
                   "--ardy-url", self.url, "--duration", str(self.duration),
                   "--seed", str(self.seed), "--timeout", str(self.timeout)]
        if self.resident:
            self._run_resident(command[2:])
            return
        # Forward status lines immediately, including playback start/finish.
        child = subprocess.Popen(command, cwd=str(self.project_root),
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 universal_newlines=True, encoding="utf-8", errors="replace")
        expired = threading.Event()
        def abort():
            expired.set()
            child.kill()
        limit = self.timeout * 4 + 3 * float(os.environ.get("ARDY_UPLOAD_TIMEOUT", "60")) + float(os.environ.get("ARDY_PLAY_TIMEOUT", "150")) + 30
        timer = threading.Timer(limit, abort)
        timer.daemon = True
        timer.start()
        tail = []
        try:
            for line in child.stdout:
                print(line.rstrip(), flush=True)
                tail.append(line.rstrip())
                tail = tail[-10:]
            code = child.wait()
        finally:
            timer.cancel()
            child.stdout.close()
            if child.poll() is None:
                child.kill()
                child.wait()
        if expired.is_set():
            raise RuntimeError("pipeline timed out; remote playback state may be unknown; inspect task logs before retrying")
        if code:
            raise RuntimeError("pipeline failed: " + "\n".join(tail))
        print("[ARDY pipeline] completed", flush=True)

    def _start_resident_worker(self):
        worker = self.pipeline_script.with_name('stream_action_worker.py' if self.stream else 'action_worker.py')
        if not worker.is_file():
            raise ValueError('Resident action_worker.py not found; deploy resident update or set ARDY_RESIDENT=0')
        self.resident_ready = threading.Event()
        self.resident_results = queue.Queue()
        env = os.environ.copy()
        env['PYTHONPATH'] = str(self.project_root / 'gmr-main')
        env.pop('PYTHONHOME', None)
        self.resident_child = subprocess.Popen(
            [self.pipeline_python, '-u', str(worker)], cwd=str(self.project_root), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, encoding='utf-8', errors='replace', bufsize=1)
        threading.Thread(target=self._read_resident_output, daemon=True).start()

    def _read_resident_output(self):
        child = self.resident_child
        try:
            for line in child.stdout:
                if line.startswith('[ARDY worker result] '):
                    self.resident_results.put(json.loads(line.split('] ', 1)[1]))
                else:
                    print(line.rstrip(), flush=True)
                    if line.startswith('[ARDY worker] READY ') or line.startswith('[ARDY stream worker] READY '):
                        self.resident_ready.set()
        finally:
            code = child.wait()
            child.stdout.close()
            self.resident_ready.set()
            self.resident_results.put({'id': None, 'returncode': code or 1,
                                       'error': 'Resident worker exited'})

    def _run_resident(self, argv):
        if not self.resident_ready.wait(timeout=90):
            raise RuntimeError('Resident worker startup timed out; inspect complete duplex log')
        child = self.resident_child
        if child.poll() is not None:
            raise RuntimeError('Resident worker is not running; inspect log and restart duplex')
        request_id = uuid.uuid4().hex
        child.stdin.write(json.dumps({'id': request_id, 'argv': argv}, ensure_ascii=False) + '\n')
        child.stdin.flush()
        limit = self.timeout * 4 + 3 * float(os.environ.get('ARDY_UPLOAD_TIMEOUT', '60')) + float(os.environ.get('ARDY_PLAY_TIMEOUT', '150')) + 30
        try:
            result = self.resident_results.get(timeout=limit)
        except queue.Empty:
            child.kill()
            raise RuntimeError('Resident task timed out; inspect task logs and robot state before retrying')
        if result.get('id') != request_id or result.get('returncode'):
            raise RuntimeError('Resident task failed; inspect task.json and stage logs: ' + str(result))
        print('[ARDY pipeline] completed', flush=True)

    def close(self):
        self.closed.set()
        if self.resident_child is not None and self.resident_child.poll() is None:
            self.resident_child.terminate()
