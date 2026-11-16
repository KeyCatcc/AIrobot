"""Open duplex's actual 16 kHz mono microphone format and report signal levels."""

import argparse
import audioop

import pyaudio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=5.0)
    parser.add_argument('--list', action='store_true', help='List input-capable PyAudio devices and exit')
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error('--seconds must be positive')
    audio = pyaudio.PyAudio()
    try:
        if args.list:
            for index in range(audio.get_device_count()):
                info = audio.get_device_info_by_index(index)
                if info.get('maxInputChannels', 0) > 0:
                    print(f'input index={index}: {info["name"]}; rate={int(info["defaultSampleRate"])}')
            return
        device = audio.get_default_input_device_info()
        print('默认输入设备：', device['name'])
        print('按 duplex 参数采集：16 kHz / 单声道 / 5 秒；请持续说话。')
        stream = audio.open(format=pyaudio.paInt16, channels=1, rate=16000,
                            input=True, frames_per_buffer=320)
        levels = []
        try:
            for _ in range(round(args.seconds * 50)):
                data = stream.read(320, exception_on_overflow=False)
                levels.append(audioop.rms(data, 2))
        finally:
            stream.stop_stream()
            stream.close()
        print('RMS 最小/最大/平均：', min(levels), max(levels), round(sum(levels) / len(levels), 1))
        print('RMS 前 20 个：', levels[:20])
    finally:
        audio.terminate()


if __name__ == '__main__':
    main()
