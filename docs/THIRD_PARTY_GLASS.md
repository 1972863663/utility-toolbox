# 毛玻璃界面参考与许可

- 视觉方向参考：[Neverlose Reloaded](https://github.com/kdt255/Neverlose-Reloaded)，本次 Qt UI 独立实现。
- 原生 Acrylic 接口参考：[PythonBlurBehind](https://github.com/Peticali/PythonBlurBehind)，`glass_toolbox/native.py` 使用相同的 Win32 窗口合成接口；保留其 MIT 许可如下。
- 研究参考：[PyQt-Frameless-Window](https://github.com/zhiyiYo/PyQt-Frameless-Window)，未引入其整个组件库。
- Qt for Python / PySide6 为当前环境已有依赖，遵循其自身 LGPL/GPL/商业许可证。构建保留动态 Qt 库与相关许可文件。
- 界面使用 HarmonyOS Sans SC（鸿蒙黑体），Copyright 2021 Huawei Device Co., Ltd.。Regular / Medium 原始字体随应用打包，仅在应用进程内加载，不安装到系统。字体及完整许可位于 `glass_toolbox/fonts/`，来源：[华为官方字体包](https://developer.huawei.com/images/download/general/HarmonyOS-Sans.zip)。字体加载失败时回退到本机 Microsoft YaHei UI / Noto Sans SC / Segoe UI。图标使用系统 Segoe MDL2 Assets。

## PythonBlurBehind — MIT License

Copyright (c) 2021 Pedro Palmeira

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
