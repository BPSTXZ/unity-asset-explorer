# Asset Explorer · 游戏资源自动识别 / 提取 / 预览 / 导出

本地 Web 应用：自动识别游戏引擎与资源格式，匹配对应解析器，提取模型 / 贴图 / 动画 / 音频，
并提供 3D 预览、贴图预览、动画信息查看与单个 / 批量导出，形成完整交互闭环。

第一版以 **Unity**（含 IL2CPP）为核心，架构上预留引擎适配器接口，可后续接入 Unreal / Godot。

## 快速开始

```bat
run.bat
```

或手动：

```bat
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
```

浏览器打开 http://127.0.0.1:8765

## 使用流程

1. 顶栏输入游戏目录，或点 **📁** 打开文件夹选择器（盘符→逐级目录→选择），或点 **示例** 载入参考游戏
   `H:\87VR游戏大厅\Romantic.Escapades.v2.0.3\Romantic Escapades` → **识别引擎**
   - 自动判定 Unity / Unreal / Godot，读取 Unity 版本与 IL2CPP / Mono 后端，列出资源文件（识别 <1s）
2. **扫描资源** → 后台建立资源清单（扫描过程中即可实时浏览）
3. 左侧按类型筛选（中文释义：模型/贴图/动画片段/音频片段/材质/着色器…）/ 顶部搜索 → 中间列表点击资源
4. 右侧预览区（默认占 44% 宽，可拖拽中缝调节侧栏与预览宽度，宽度记忆）：
   - 模型 → three.js 3D 预览（含蒙皮），线框 / 重置视角
   - 贴图 / 精灵图 / 立方体贴图 → 贴图预览
   - 动画片段 → Generic 骨骼播放；Humanoid 显示肌肉曲线信息
   - 音频片段 → 播放；文本资源 / 材质等 → 文本 / 类型树 JSON
5. 勾选多项 → **导出选中**，或 **导出当前筛选**；可选输出目录与模型格式（GLB / OBJ）

导出默认落在 `exports\<游戏名>\` 下，按类型分目录。

## 目录结构

```
backend/
  main.py              FastAPI 路由 + 扫描/导出任务调度
  detectors/           引擎自动识别（Unity/Unreal/Godot 特征）
  parsers/unity_parser.py   UnityPy 资源扫描与清单
  exporters/gltf.py    Mesh/蒙皮/动画 → glTF/GLB；Humanoid 无损数据导出
frontend/              纯静态界面（three.js 本地 vendor，无需构建）
exports/               导出结果
.cache/                预览缓存（PNG/GLB）
```

## 技术要点

- 解析核心 [UnityPy](https://github.com/UnityPy/UnityPy)，纯 Python，mesh/贴图/动画/音频直接进自有界面
- 自写 glTF 导出器：左手系→右手系转换、蒙皮 JOINTS/WEIGHTS/ inverseBindMatrices、Generic 动画曲线
- Humanoid 肌肉动画暂以无损 JSON 导出（绑定 + 常量/密集/编码流曲线），骨骼重定向列入后续版本
- 扫描 / 导出均为后台线程 + 进度轮询，不阻塞界面；刷新页面可恢复扫描进度

## 后续优化方向（Roadmap）

- Humanoid 动画骨骼重定向预览（结合 Avatar / Il2CppDumper 类型信息）
- Unreal 适配器（封装 [CUE4Parse CLI](https://github.com/joric/CUE4Parse.CLI) / [FModelCLI](https://github.com/Herselfta/FModelCLI)）
- 材质 + 贴图自动关联导出（glTF 带 PBR 材质）、模型依赖图
- 资源去重（哈希）、AES 加密 bundle 密钥管理、导出预设（Blender / Unity 回导）
- 大批量扫描的多进程加速与增量缓存