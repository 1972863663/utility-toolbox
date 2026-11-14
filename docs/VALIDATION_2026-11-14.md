# v2026.11.14.1 关闭功能修复验证

本轮：**359 passed，3 deselected，66.03 秒**；额外排除旧 Tk 可见界面的 `test_toolbox_ui.py`（5 项）。未操作桌面鼠标键盘，未结束用户正在使用的游戏、加速器或其他应用。

## 已复现并修正

- 关闭所选未采用分组中保存的实际程序路径，导致与关闭整组行为不一致。
- 分组关闭过滤掉文件夹目标；现在与拖入关闭共用目标解析和路径匹配。
- 失败详情会被末尾观察消息覆盖；现保留失败计数、详情并持久记录日志。
- taskkill 不可用时没有后备路径；现在重新核对进程路径，通过原生 TerminateProcess 终止并等待退出。
- 关闭按钮仍弹确认框；现点击直接执行。启动批次进行中也可发起关闭，取消尚未启动的项目。
- 新增独立拖入关闭区域，普通文件使用 Windows Restart Manager 查找占用者。未修改文件内容或强行关闭文件句柄。

## 实际验证

- 临时编译的 Windows EXE：程序、快捷方式、目录、整组关闭、启动器退出、守护程序重新拉起和超时场景。
- Qt 按钮 → 实际业务队列 → 真实临时进程关闭；异目录同名进程保留。
- 同一个临时 EXE 启动两份实例，各自独占一份文件：拖入目标文件只关闭对应实例，另一份实例继续运行，文件内容保留。
- 模拟 taskkill 文件缺失，真实临时进程由原生 API 成功结束。
- 拖放专项 11 项通过（22.95 秒）：通过 QApplication.sendEvent 发送 QDragEnterEvent / QDropEvent，覆盖 EXE、快捷方式、含子目录的应用目录、多目标、普通文件占用、taskkill 缺失后备、远程路径与重复请求拒绝，以及按钮链路。目标为真实临时进程；无关同名实例、路径前缀相似的其他目录实例均保留。未操作系统鼠标。
- 成品内 utility_toolbox、process_file_locks、glass_toolbox.pages_workspace 三个模块与本次源码编译结果一致。
- 本机新版本已按自启动参数启动到隐藏托盘；46 个用户 JSON 文件哈希保持不变，Ins 与注册表自启动项保留。

主 EXE SHA256：`541B13147F98B469F10F8E08DA4754B048C6C64B7916817300D3A83C2752B45C`。

## 边界

没有通过关闭用户实际软件复现其此前那一次失败，因此不能断言上述某一缺陷就是该次失败的唯一原因。现在失败会记录到 data/UtilityToolbox/toolbox.log。

没有重启 Windows，未重测本次实体 Insert 按键。三个需要真实桌面置顶的 Qt 测试未运行。受保护进程、管理员权限不足、服务自动重新拉起仍可能无法关闭；不会隐藏这类失败。脚本路径暂不支持推断解释器实例，请使用实际 EXE 或应用专用目录。目录模式匹配目录内运行的程序，不枚举所有目录句柄。

实现参考：已对照 [FileUnlocker 源码](https://github.com/poyitu/FileUnlocker/blob/main/file_unlocker.py) 中 `_on_drop`、`FileLockDetector._detect_rm`、`ProcessManager.kill_process` 的拖放入口、占用枚举、终止后备与退出验证。按本工具的“松手直接关闭”要求适配，自行调用微软公开的 [Restart Manager](https://learn.microsoft.com/en-us/windows/win32/api/restartmanager/nf-restartmanager-rmgetlist) 与 [TerminateProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess)，未复制第三方项目代码或捆绑其程序。

---

以下是上一个发布版本的历史验证记录，哈希及数量只对应 v2026.11.14。

# v2026.11.14 验证记录

发布基于 2026-11-13 完成的代码验证：341 项通过，0 失败、0 错误；现有 22 个模块共 321 项，新增界面连接测试 20 项。

覆盖：11 个工具路由与输入保留；七种重命名规则及撤回；字幕同步、历史保护及停止时序；真实临时照片扫描与复制；启动器分组、路径和进程关闭范围；麦克风设置；音频输出参数和失败提示；宏参数、监听器清理、输入释放；自启动错误回退；存档管理器定位与资料保护；后台任务和配置并发保护。

Ins、F8、Ctrl+Alt+Space 分别通过模拟 WM_HOTKEY 连续切换 20 次，验证每次唤出请求置顶以及录制当前快捷键时不隐藏窗口。

Qt 在 offscreen 环境执行，不操作桌面鼠标键盘。Windows 进程关闭的 10 项集成测试只启动/结束自行编译的临时测试进程。

## 验证边界

- 3 项依赖实际桌面置顶的 Qt 测试和旧 Tk 可见界面的 5 项测试未在此次无干扰检查中执行。
- 音频切换、麦克风静音、宏回放、自启动写入的逻辑验证使用模拟接口，未操作真实设备。
- 未在本轮运行 Game Save Manager 的真实备份恢复，也未重验毛玻璃视觉效果。
- 原生毛玻璃、单键切换和字体显示曾在此前开发阶段检查；不能据此保证任意电脑环境。

## 成品一致性

主 EXE SHA256：`ED15B631B69C197EF6F8A9636EBB39F5BD74E209513DBB7B036286F738DCF5F3`。

已提取主 EXE 内 10 个 glass_toolbox 模块，与当前源码编译结果比较一致；两份鸿蒙字体与许可证字节一致。测试前后用户 JSON 配置和现用 EXE 哈希未变化。
