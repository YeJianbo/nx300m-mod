# NX300M 遥控服务适配

源项目：`mewlips/nx-remote-controller-mod@1a931e2569e11154415bdf4685af06dfbcc268cf`，AGPL-3.0。原始代码与 LICENSE 在 `work/reference/nx-remote-controller`。

`remote.c` 使用其 `nx_model.c`、`status.c`、`util.c`、`command.c`，复用 Mongoose 6.5 HTTP 服务与上游 OSD 线格式。适配部分改用 NX300M 原厂 Xlib/XTest 获取实际原厂屏幕并发送输入；没有复用上游针对其他硬件的 `/dev/mem` 帧缓冲地址。

`compat.h`／`compat.c` 适配 GCC 14 头文件与原厂 glibc 2.13 的接口名称差异，保留 32 位时间、64 位文件偏移。stat64 入口转发原厂 __xstat64／__lxstat64／__fxstat64，fcntl64 转发旧 fcntl，不替换原厂运行库。

网页来自 Metol 固定版本，`tools/build_remote.py` 从未改动的参考目录生成适配副本。改动包含同源入口、NX300M 型号识别、无 MAC 时的本机实例标识、未接通能力、未知电量、无缩略图时加载原图、启动期间的状态检查及字体路径。参考网页保留原样。

构建、接口、验证和未完成项见 [REMOTE.md](../../../docs/REMOTE.md)。
