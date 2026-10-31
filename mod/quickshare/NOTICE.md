本目录 Quick Share 协议核心基于 Martichou/rquickshare：

- URL: https://github.com/Martichou/rquickshare
- commit: 378d8ae969941bee4bf60ad34ac9cf8bb7005eb7
- 上游核心 crate: rqs_lib 0.11.5
- 许可证: GPL-3.0，见 LICENSE
- 原始检出: upstream-rquickshare
- 本地修改核心: core
- 相机命令行集成: src/main.rs

本地修改内容及运行边界见 ../../docs/QUICKSHARE.md。Rust 依赖各自许可保留于其源码；Cargo.lock 记录具体依赖版本与 Git revision。此原型尚未正式发布。

2026-10-20 互通修复参考：rquickshare PR #439 的短 mDNS 信息处理；Bada/LibreDrop PR #108 的三星附件 metadata 与 safe-disconnect 流程（本地 Rust 重新实现）；Google Nearby 的 PAYLOAD_ACK、safe_to_disconnect_version 与 IntroductionFrame.use_case protobuf 字段。具体来源与实测范围见 docs/QUICKSHARE.md。
