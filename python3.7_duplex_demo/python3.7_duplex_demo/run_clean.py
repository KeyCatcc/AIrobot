"""Run duplex with a concise console and a complete diagnostic log.

Usage: python -u run_clean.py --config config.toml
       python -u run_clean.py --verbose --config config.toml
"""
import argparse
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import uuid
from collections import deque


def console_line(line):
    text = line.strip()
    if not text:
        return None
    # Native audio diagnostics remain available in the complete log.
    if text.startswith(('ALSA lib ', 'JACK ', 'jack ', 'Cannot connect to server',
                        'JackShmReadWritePtr', 'jack server is not running')):
        return None
    if text.startswith('[ASR completed]'):
        return '[用户] ' + text.split('transcript=', 1)[-1]
    if text.startswith('[Chat done]'):
        return '[回复] ' + text.split('text=', 1)[-1]
    if text.startswith(('[ARDY', '[Robot dialogue]', '[error]', '[ASR failed]', '[TTS delta]',
                        '[response.canceled]', '[RAI bridge]', '[RAI pair]',
                        '[GESTURE', 'Session created', '[session.closed]',
                        'session.close error:')):
        return text
    # The pipeline prints the local CSV path on its own line.
    if text.endswith('.csv') and (text.startswith('/') or ':\\' in text):
        return '[本地 CSV] ' + text
    if text.startswith(('Traceback ', 'ERROR:', 'Error:', 'usage:')) or 'Error:' in text:
        return text
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--log-file', type=Path)
    args, duplex_args = parser.parse_known_args()
    log_path = args.log_file or Path('logs') / (
        'duplex_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6] + '.log')
    log_path = log_path.resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print('[日志] 完整输出：' + str(log_path), flush=True)
    print('[启动] duplex；按 Ctrl+C 退出。', flush=True)
    command = [sys.executable, '-u', str(Path(__file__).with_name('main.py')), *duplex_args]
    tail = deque(maxlen=35)
    with log_path.open('w', encoding='utf-8') as log:
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding='utf-8', errors='replace', bufsize=1)
        interrupted = False
        try:
            for line in child.stdout:
                log.write(line)
                log.flush()
                tail.append(line.rstrip())
                visible = line.rstrip() if args.verbose else console_line(line)
                if visible is not None:
                    print(visible, flush=True)
            code = child.wait()
        except KeyboardInterrupt:
            interrupted = True
            # Ctrl+C also reaches the child in the foreground process group.
            print('\n[退出] 正在关闭 duplex…', flush=True)
            try:
                code = child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.terminate()
                try:
                    code = child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    code = child.wait()
        finally:
            child.stdout.close()
    if code and not interrupted:
        print('[失败] duplex 退出，返回码：' + str(code), flush=True)
        print('[错误上下文] 最后 35 行原始输出：', flush=True)
        for line in tail:
            print(line, flush=True)
    else:
        print('[退出] duplex 已关闭。', flush=True)
    return 130 if interrupted else code


if __name__ == '__main__':
    raise SystemExit(main())
