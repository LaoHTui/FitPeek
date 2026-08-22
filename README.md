<p align="center">
  <img src="assets/fitpeek.png" alt="FitPeek icon" width="120">
</p>

<h1 align="center">FitPeek</h1>

<p align="center">
  A read-only FITS inspection and quick-look timing analysis desktop app for Windows.<br>
  面向 Windows 的只读 FITS 检查与时域快速分析桌面工具。
</p>

<p align="center">
  <a href="#中文">中文</a> | <a href="#english">English</a>
</p>

---

## 中文

### 简介

FitPeek 是一个面向高能时域天文数据的本地、只读、零代码 FITS 快速检查工具。它适合在正式进入 Python、HEASoft 或其他分析管线之前，用来浏览文件结构、比较 Header、检查事件表，并快速生成光变曲线。

FitPeek 不会修改原始 FITS 文件。

### 主要功能

- 多文件 Session：支持拖放、最近打开记录和会话恢复。
- FITS 结构浏览：查看 HDU 类型、行数、形状和文件摘要。
- Header 查看与对比：搜索 Header，并比较任意两个文件或 HDU。
- 表格预览：支持首尾行、自定义范围、全列筛选、数值排序和选中行 CSV 导出。
- 科学数据检查：提供 GTI 和 EBOUNDS 专用视图。
- 光变曲线：支持多个独立窗口，以及同一文件重复打开对比。
- 事件筛选：支持时间范围、DT、GTI、能段、FLAG 和 EVT_TYPE。
- 阶梯直方图预览：保留 Poisson 误差棒、T0 标记和稠密数据保峰降采样。
- 导出事件、完整光变数据和 PNG/JPEG 图像；图像内嵌时间窗、DT、能段、探测器、筛选条件和误差定义。
- 浅色、深色和跟随系统主题。
- About 页面显示软件版本、作者署名、版权、许可证和源码地址。
- Windows FITS 文件关联脚本。

支持的常见扩展名包括：`.fits`、`.fit`、`.fits.gz`、`.evt`、`.pha`、`.rsp`、`.rsp2` 和 `.rm`。

### 下载与安装

当前稳定版本：**0.3.0**。详细变更见 [CHANGELOG.md](CHANGELOG.md)。

1. 打开本仓库的 **Releases** 页面。
2. 下载 `FitPeek_Portable.zip`。
3. 将 ZIP 完整解压到一个新文件夹。
4. 运行其中的 `FitPeek.exe`。

`FitPeek.exe` 必须和 `_internal` 文件夹放在一起。不要只把 EXE 单独移动到其他位置。

如果 Windows SmartScreen 提示未知发布者，这是因为当前程序没有商业代码签名。可以检查下载来源和 SHA-256 后，选择“更多信息 -> 仍要运行”。

### 文件关联（可选）

在解压目录中双击 `Bind-FitPeek.cmd`，即可为当前 Windows 用户注册常见 FITS 扩展名。注册后仍可通过 Windows 的“打开方式”修改默认程序。

### 从源码运行

需要 Windows 和 Python 3.12 或更新版本：

```powershell
git clone https://github.com/LaoHTui/FitPeek.git
cd FitPeek
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

### 构建便携包

```powershell
.\build.ps1
```

国内网络需要指定 PyPI 镜像时：

```powershell
.\build.ps1 -IndexUrl https://pypi.tuna.tsinghua.edu.cn/simple
```

构建结果：

- `dist\FitPeek\FitPeek.exe`
- `FitPeek_Portable.zip`

也可以通过 `FITPEEK_PYTHON` 环境变量或 `-PythonPath` 参数指定 Python：

```powershell
.\build.ps1 -PythonPath C:\Python312\python.exe
```

### 测试

```powershell
.\.venv\Scripts\python.exe smoke_test.py
.\.venv\Scripts\python.exe ui_smoke_test.py
```

测试会在临时目录生成自己的 FITS 样本，不需要向仓库提交真实观测数据。

### 科学说明与限制

- 当前光变曲线显示原始 `Counts / bin` 或 `Count rate / s`，尚未进行背景扣除，因此不能称为 Net Counts Rate。
- 只保留完整、等宽的 DT bin；不足一个 DT 的尾段会被报告并忽略。
- 表格筛选、排序和选中行导出只作用于当前预览窗口，最多 5,000 行。
- 稠密光变曲线只在屏幕预览时降采样；导出的数组保持完整。
- 能段换算依赖 EBOUNDS，或事件表中的直接 ENERGY 列。
- 光变曲线图中的探测器、仪器、目标和观测编号直接取自 FITS Header；缺失字段会明确标记，而不会推断。

### 项目结构

```text
FitPeek/
|-- app.py                 主窗口与 Session 管理
|-- app_info.py            软件版本、作者与项目元数据
|-- analysis_window.py     光变曲线窗口和导出
|-- light_curve.py         光变曲线计算
|-- fits_reader.py         只读 FITS 访问
|-- table_model.py         表格预览、筛选和排序
|-- header_compare.py      Header 对比窗口
|-- assets/                应用图标
|-- smoke_test.py          核心功能测试
|-- ui_smoke_test.py       UI 工作流测试
|-- build.ps1              Windows 打包脚本
`-- .github/workflows/     GitHub CI 与 Release
```

### 发布新版本

完成修改并推送到 `main` 后，创建版本 tag：

```powershell
git tag v0.1.0
git push origin v0.1.0
```

GitHub Actions 会自动运行 Windows 构建，创建 Release，并上传：

- `FitPeek_Portable.zip`
- `FitPeek_Portable.zip.sha256`

### 许可证

本项目基于 [MIT License](LICENSE) 开源。

---

## English

### Overview

FitPeek is a local, read-only, zero-code FITS inspection tool for high-energy time-domain astronomy. It is designed for quick checks before moving into Python, HEASoft, or a full scientific analysis pipeline: inspect file structure, compare headers, preview event tables, and generate light curves.

FitPeek never modifies the source FITS file.

### Features

- Multi-file sessions with drag and drop, recent files, and session restore.
- HDU structure, row count, shape, and file summary inspection.
- Searchable FITS headers and side-by-side comparison of any two HDUs.
- Windowed table previews with filtering, numeric sorting, and selected-row CSV export.
- Dedicated GTI and EBOUNDS views.
- Multiple independent light-curve windows, including multiple views of the same file.
- Time, DT, GTI, energy, FLAG, and EVT_TYPE filtering.
- Step-histogram previews with Poisson error bars, a T0 marker, and peak-preserving display downsampling.
- Event, full light-curve data, and PNG/JPEG image export.
- System, light, and dark themes.
- An About page with version, author, copyright, license, and source information.
- Optional Windows file-association scripts.

Common supported extensions include `.fits`, `.fit`, `.fits.gz`, `.evt`, `.pha`, `.rsp`, `.rsp2`, and `.rm`.

### Download and Install

1. Open the repository's **Releases** page.
2. Download `FitPeek_Portable.zip`.
3. Extract the complete ZIP into a new folder.
4. Run `FitPeek.exe` from that folder.

Keep `FitPeek.exe` beside the `_internal` directory. The executable is not standalone and must not be moved out by itself.

Windows SmartScreen may report an unknown publisher because current builds are not commercially code-signed. Verify the download source and SHA-256 checksum before choosing **More info -> Run anyway**.

### Optional File Associations

Run `Bind-FitPeek.cmd` from the extracted directory to register common FITS extensions for the current Windows user. Windows **Open with** settings can still be used to change the default application.

### Run from Source

Windows and Python 3.12 or newer are required:

```powershell
git clone https://github.com/LaoHTui/FitPeek.git
cd FitPeek
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

### Build the Portable Package

```powershell
.\build.ps1
```

To use a custom package index:

```powershell
.\build.ps1 -IndexUrl https://pypi.tuna.tsinghua.edu.cn/simple
```

Build outputs:

- `dist\FitPeek\FitPeek.exe`
- `FitPeek_Portable.zip`

An explicit Python executable can be provided through `FITPEEK_PYTHON` or `-PythonPath`:

```powershell
.\build.ps1 -PythonPath C:\Python312\python.exe
```

### Tests

```powershell
.\.venv\Scripts\python.exe smoke_test.py
.\.venv\Scripts\python.exe ui_smoke_test.py
```

The tests create temporary FITS samples and do not require real observation data in the repository.

### Scientific Notes and Limitations

- Light curves currently show raw `Counts / bin` or `Count rate / s`; no background subtraction is performed, so the result is not a net count rate.
- Only complete, equal-width DT bins are retained. A shorter trailing interval is reported and omitted.
- Table filtering, sorting, and selected-row export operate on the current preview window of up to 5,000 rows.
- Dense light curves are downsampled only for display. Exported arrays remain complete.
- Energy conversion requires EBOUNDS or a direct ENERGY column in the event table.

### Release a Version

After pushing tested changes to `main`, create and push a version tag:

```powershell
git tag v0.1.0
git push origin v0.1.0
```

GitHub Actions will build the Windows package, create a GitHub Release, and upload the portable ZIP and its SHA-256 checksum.

### License

This project is released under the [MIT License](LICENSE).
