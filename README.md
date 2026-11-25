# Asset Explorer · 游戏资源自动识别 / 提取 / 预览 / 导出

本地 Web 应用：自动识别游戏引擎与资源格式，提取模型 / 贴图 / 动画 / 音频 / 角色，
提供 3D 预览、角色组装与材质贴图、动画重定向播放、CG 画廊、收藏管理与单个 / 批量导出，形成完整交互闭环。

以 **Unity**（含 IL2CPP）为核心，支持 **PC 游戏目录**与 **安卓 APK / OBB** 容器；架构预留引擎适配器接口，可后续接入 Unreal / Godot。

## 快速开始

**方式一：源码运行**

```bat
run.bat
```

自动创建 Python 3.12 虚拟环境、安装依赖、启动服务并打开浏览器（http://127.0.0.1:8765）。

或手动：

```bat
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
```

**方式二：免安装 exe**

```bat
build.bat
```

生成单文件 `dist\AssetExplorer.exe`（约 30 MB，无需 Python 环境），双击即用、自动开浏览器；
缓存与导出落在 exe 同级目录。分发只需该 exe 一个文件。

## 使用流程

1. **选择资源**：顶栏输入路径，或点 📁 打开选择器（可选目录，也可直接选中 apk / obb / assets 等文件），或点「示例」载入参考游戏 → **识别引擎**。
   - 自动判定 Unity / Unreal / Godot，读取 Unity 版本与 IL2CPP / Mono 后端，列出资源文件。
   - APK / OBB 自动解包（ZIP）或原地直读（UnityFS bundle），复用 Unity 管线。
2. **扫描资源** → 后台建立清单，扫描中即可实时浏览；支持 **暂停 / 继续**。
3. **浏览清单**：左侧按类型筛选（中文释义：模型/贴图/动画片段/音频片段/材质/着色器…）/ 顶部搜索 / 名称·大小排序，200 项分页；图片类资源名称右侧显示缩略图。
4. **预览**（右侧，宽度可拖拽并记忆）：
   - 模型 → three.js 3D 预览（含蒙皮），线框 / 重置视角；
   - 角色 → **角色组装**（骨骼+蒙皮多部件）、**材质贴图**（递归加载依赖 bundle 还原正确材质）、**动画播放**；
   - 动画播放面板 → 选择兼容 clip（按骨骼路径 CRC 匹配）、播放/暂停、可拖动进度条、**导出当前帧姿态**；
   - 贴图 / 精灵图 / 立方体贴图 → 图片预览；音频 → 播放；文本 / 材质 → 文本 / 类型树 JSON。
5. **CG 画廊**：侧栏「CG 画廊」→ 全屏大图网格（「游戏CG」按 bundle 命名筛选 / 「仅大图」/ 搜索 / 分页）→ 点图进全屏灯箱（缩放 / 平移 / 上一张下一张 / 下载）。
6. **收藏**：列表点 ☆ 收藏，自定义别名（仅个人辨识，不影响原命名与解析）；侧栏「我的收藏」按类型自动分组，支持重命名 / 取消，折叠状态记忆。
7. **导出**：勾选多项 → **导出选中**，或 **导出当前筛选**；可选输出目录与模型格式（GLB / OBJ）。默认落在 `exports\<游戏名>\`，按类型分目录。
8. **移除项目**：侧栏项目名旁 ✕ → 停止后台任务、清空清单与解析缓存以释放内存（收藏保留）。

## 目录结构

```
backend/
  main.py                  FastAPI 路由 + 扫描/导出/动画匹配任务调度
  paths.py                 开发态 / 打包态路径分离（只读资源 vs 可写数据）
  detectors/               引擎识别（Unity/Unreal/Godot）+ APK/OBB 容器解包
  parsers/unity_parser.py  UnityPy 资源扫描与清单（可暂停）
  exporters/
    gltf.py                Mesh/蒙皮/动画 → glTF/GLB
    character.py           角色组装 + 材质贴图构建
    anim_retarget.py       Generic 动画曲线解码与重定向
frontend/                  纯静态界面（three.js 本地 vendor，无需构建）
launcher.py                桌面 exe 启动入口
asset_explorer.spec        PyInstaller 打包配置
run.bat / build.bat        源码运行 / 打包 exe
exports/                   导出结果
.cache/                    预览/缩略图缓存、会话、收藏
```

## 技术要点

- 解析核心 [UnityPy](https://github.com/UnityPy/UnityPy)，mesh / 贴图 / 动画 / 音频直接进自有界面。
- 自写 glTF 导出器：左手系→右手系转换、蒙皮 JOINTS/WEIGHTS/inverseBindMatrices、Generic 动画曲线。
- 角色动画重定向：Generic Transform 曲线解码（streamed/dense/constant），按骨骼路径 CRC32 匹配 clip 与角色骨架，支持播放与当前帧姿态导出。
- 材质贴图：外部 PPtr 依赖递归加载（`AssetBundle.m_Dependencies`），还原跨 bundle 的正确材质。
- APK / OBB：ZIP 解出 `assets/bin/Data` 与 `StreamingAssets/aa` 的 bundle；UnityFS 魔数的 OBB 原地直读。
- 扫描 / 导出 / 动画匹配均为后台线程 + 进度轮询，不阻塞界面；刷新页面可恢复会话。
- 缩略图 / CG 画廊：小尺寸磁盘缓存 + 懒加载 + 并发解码限制，控制内存占用。
- PyInstaller 单文件打包：收集 UnityPy C 扩展与 archspec 数据文件，区分只读资源与可写数据目录。

## 后续优化方向（Roadmap）

- Unreal 适配器（封装 [CUE4Parse CLI](https://github.com/joric/CUE4Parse.CLI) / [FModelCLI](https://github.com/Herselfta/FModelCLI)）、Godot 适配器
- 资源去重（哈希）、AES 加密 bundle 密钥管理、导出预设（Blender / Unity 回导）
- 大批量扫描的多进程加速与增量缓存
- Spine 骨骼动画 CG 播放（部分游戏 CG 为 Spine 动画而非静图）