# 机器人动作执行最小包

从 serverobot-v2 选择性提取，原项目未修改。保留脚本、动作库和电机映射的目录关系，供 RAI 的 serverobot 适配器使用。

## 范围

- scripts/test_custom_actions.py：原动作 CLI。
- modules/base.py、motor_map.py、motor_pub.py、action_pub.py：数据结构、电机映射和 ROS 发布依赖。
- utils/utils.py：motor_map 使用的 YAML 读取依赖。
- configs/gestures/：动作及姿态 JSON。
- configs/robot/ymbot_h2/mapping/motor.yaml：原电机映射。
- configs/config.yaml：只保留原配置的 shm 段，不包含语音模型或访问凭据。
- SOURCE_MANIFEST.json：提取来源及原文件 SHA256。

为避免改动现有链路，本次按文件保留依赖，base/utils/motor_pub 中仍有未使用函数。当前只开放 welcome_gesture，JSON 中其他动作不代表已授权给模型。

## 环境

dry-run 需要 Python 3.10+、numpy、PyYAML，不需要导入 ROS 发布模块。
实机发布额外需要已有 ROS 2 环境、std_msgs，以及编译并 source 后的 ymbot_motor_control（MotorTrajectory）。这些不能通过 requirements.txt 代替。

本包不包含机器人驱动、轨迹订阅端、手指驱动或 ROS 消息包；真机侧沿用已有部署。

## WSL 使用

将本目录复制为 ~/rai/robot-action-runtime。

先运行本包自身的 dry-run：

```bash
cd ~/rai/robot-action-runtime
python scripts/test_custom_actions.py --action welcome_gesture --amplitude 0.2 --skip-fingers
```

再按桥接服务原命令启动，将 RAI_SERVEROBOT_ROOT 改为 "$HOME/rai/robot-action-runtime"，不设置 RAI_SERVEROBOT_EXECUTE。

## 已知边界

保留上游行为：回到相对零位对应的电机中位，而非 JSON rest；发布后按估计时长等待，无物理完成确认；action_pub 中断时不显式发送停止；ROS_DOMAIN_ID 固定为 10。

脚本原默认 amplitude 为 0.80，与帮助文字不同，所以命令和适配器都显式传入 0.2。动作库和电机映射仍需按实际机型验证。

本包用于调用链联调，未因此获得新的真机验证结论。
