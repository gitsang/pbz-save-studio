# 影之刃零 · 存档工坊

> v1.0.0 · Windows 桌面小工具，用来查看和修改《影之刃零》(Phantom Blade Zero) 的本地存档：
> Boss 战报（挑战/击杀/身陨/最佳用时）、难度、物品数量，改完自动备份。

## 特性

- **原生桌面窗口**（WebView2），无控制台、不打开浏览器
- **Boss 战报**：按难度/周目列出挑战次数、击杀数、身陨、最佳用时，可直接改；表头点击排序
- **物品**：275 项物品数量可查可改，支持筛选
- **安全**：写前自动备份 `.bak-时间戳`，未改动槽位原样复用原始密文块，新文件先全量校验再原子替换
- **离线**：本地读取存档，只监听 `127.0.0.1`，不联网、不上传

## 免责声明

- 仅用于**单人离线**存档，请自行备份；使用风险自负
- 仓库**不包含**任何游戏本体文件、资源或 RAD Oodle 运行库，需你本机已安装游戏
- 与 S-GAME / 灵游坊无关，非官方工具

## 结论：能改，而且不用内存修改器

存档 `PBZSaveGame.sav` 是 **PBZS v3** 自定义容器：12 字节明文头 + AES-256-GCM 加密目录块 + 每槽位独立加密块（Oodle Kraken 压缩，zlib 也接受）。密钥不是账号绑定的，而是 build 常量——从 `platform\steam\PBZ.exe` 里 `sealed but block is PLAINTEXT` 锚点后 64 字节做 XOR 就能还原。所以你改完重新加密写回，游戏照读。

真正的数据全在槽位 JSON 里，你要的三样东西都有明确字段：

| 你要的 | 字段 | 说明 |
|---|---|---|
| 难度 | `PBZGameSystem.DifficultModeByte` | 0=Handicapped 1=入阵者 2=破局者 3=逆天者 4=六十六天（枚举取自 PBZ.exe 的 `EDifficultMode`） |
| Boss 身陨次数 | `BossRecord.<Boss>.Records["<周目>_<难度>"].AttemptCount / KillCount` | 身陨 = AttemptCount − KillCount；键名是 `0_3` 这种「周目_难度」 |
| 击杀用时 | 同上 `.BestTimeMs` | 毫秒 |
| 总死亡数 | `PBZDeathMeta.DiedTimes` | 每档一份 |
| 生涯战报 | `__Advanced__.BossRecord.<Boss>.Records["<难度>"]` | 多一个 `Cycle` 字段，键名只有难度没有周目 |

注意：**当前档的 BossRecord 和 `__Advanced__` 的生涯战报是两套数据**，界面上的「身陨/最快击杀」两处都可能用到，所以工具默认两处一起改。

### 两套战报的区别

| | 单档战报 `BossRecord` | 生涯战报 `__Advanced__.BossRecord` |
|---|---|---|
| 位置 | 每个 ManualSave/AutoSave 各一份，跟着存档走 | 存档包里一个特殊槽位，全账号唯一，不属于任何一档 |
| 键名 | `"<周目>_<难度>"`，如 `0_3` | 只有难度，如 `"3"`；周目写在记录里的 `Cycle` 字段 |
| 生命周期 | 换档就换一套；读旧档看到的就是旧数据 | 只增不减，跨周目、跨存档累计 |
| 回档会回退吗 | 会（回到那时的快照） | **不会**——它在 `__Advanced__` 里，回档/覆盖单档都动不到它 |

实测证据（就是你的档）：`ZuoShang` 单档是 `1 战 1 杀`，生涯是 `2 战 2 杀`；`WuXuan` 单档只有 `0_3` 一条，生涯里 `1` 和 `3` 两个难度都有。也就是说你之前用备份回过档，单档被拉回去了，生涯那本账还在往前走。

### Boss 中文名

`boss_names.json` 是内部键 → 中文名/英文名的对照表，工具和界面都读它：

```json
"BP_Monster_WuXuan": { "cn": "堕落将军：武轩", "en": "Wu Xuan, the Fallen General", "verified": true }
```

- 名字取自**小黑盒「影之刃零 存档解析」的 Boss 图鉴**（75 条官方译名，比英文 wiki 的转译准）
- 内部键是角色名拼音（`GuWanYue`=骨婉约、`Zhuobi`=拙壁、`YuZan`=玉瓒、`CanQu`=残躯），拼音对得上的直接确认
- 对不上拼音的（如 `LuanShi`、`GuiChai_Yellow_MoDao`），用**最佳用时**跟图鉴卡片对齐：`LuanShi` 2:05.526 ↔ 图鉴「拳痴：铁狂 2:05」，`GuiChai_Yellow_MoDao` 7:40.974 ↔ 「鬼差高手：斩骑 7:40」
- `verified: false` 的条目界面会显示 `?`：`HookMan`（钩者？）、`GuiChai_Yellow_ShuangGou`（勾魂？）、`GuiChai_Red_P2`、`EMei_Sword_Top`、`HuaShan_*` —— 这几条没能跟图鉴唯一对上
- 改名直接编辑这个 JSON，重启生效（exe 旁边有一份，源码旁也有一份）。文件里的 `_version` 用于升级时自动覆盖旧版名字表；你自己改过的名字建议把 `_version` 一起调大，防止被覆盖

### 单独改某个档的难度

- 网页版：Boss 战报卡片标题右边有个「此档难度」下拉 —— **只改当前选中的这个槽位**
- 网页版整片改：上方「全局修改 → 难度 + 作用范围」
- 表格里每一行的难度胶囊也是可点的下拉，作用是把**这一条战绩**挪到别的难度下（键名 `0_3` → `0_2`，生涯战报同步跟着改名）
- 命令行：`python pbz_tool.py set-difficulty 3 --slots ManualSave17`、`python pbz_tool.py move-records --from 1 --to 3`

## 用法 0：双击 exe（桌面窗口，无控制台、不进浏览器）

`dist\影之刃零-存档工坊.exe` —— 双击直接弹出一个原生窗口（基于 WebView2 / Edge 内核），没有黑框、不打开浏览器。窗口内就是完整界面，关窗口即退出。

- 技术上：进程内起一个只监听 `127.0.0.1` 的临时端口服务，用 pywebview 把它装进窗口；端口随机，不对外暴露
- 启动会在 exe 旁生成 `app.log`（记录本次使用的本地地址，排障用）
- 依赖系统自带的 WebView2 Runtime（Win10 1903+ / Win11 默认都有；缺了会弹窗提示，装微软 Evergreen 版即可）
- `影之刃零-存档工坊-server.exe` 是同一套代码的「控制台 + 浏览器」旧版，留着调试用

### 界面结构（v1.3）

```
┌──────────────────────────────────────────────────────┐
│ 刃 存档工坊 影之刃零 v1.3   [运行状态]   ……存档路径… 刷新 │
├──────────┬───────────────────────────────────────────┤
│ 存档备份   │  Boss 战报 34 │ 物品 277       当前档信息 │
│ [下拉][还原]│ ───────────────────────────────────────── │
│ 存档槽位   │  Boss | 难度 | 挑战 | 击杀 | 身陨 | 用时  │
│ · AutoSave0│   …（点表头排序，只这里滚动）              │
│ · …        │                                          │
├──────────┴───────────────────────────────────────────┤
│ 待提交：N 处修改   最近一条消息     [丢弃][预览][写入]  │
└──────────────────────────────────────────────────────┘
```

- 外层不滚动：`html/body` 固定 100vh + `overflow:hidden`，只有槽位列表、Boss 表格、物品表格各自内部滚动
- 备份选择与还原放在左栏顶部，写入前仍会自动生成 `.bak-时间戳`
- 底部操作栏固定高度、左右 22px 内边距，不再贴边

## 用法 0.5：双击 exe（旧版：控制台 + 浏览器）

`dist\影之刃零-存档工坊.exe` —— 双击即可，无需装 Python。启动后自动打开浏览器到 `http://127.0.0.1:8765`，黑窗口就是服务本体，关掉窗口即退出。

首次启动会在 exe 旁边生成两个小文件：

- `pbz_aes_key.txt`：存档 AES 密钥缓存（从游戏主程序算出来的，删了会自动重算）
- `oodle_path.txt`：借用到的 `oo2core_*_win64.dll` 路径（自动从 Steam 库里找；找不到会弹框让你手动选一次）

> PyInstaller 打的包没做数字签名，Windows SmartScreen 或杀软可能弹一次警告，选「仍要运行 / 允许」即可。介意的话用下面的源码方式跑。

## 用法 A：网页版（源码）

```bash
cd pbz
python pbz_web.py              # 自动开 http://127.0.0.1:8765
python pbz_web.py --port 9000 --no-open
```

界面能力：左侧槽位列表 → 右侧 Boss 战报表格，直接改「挑战 / 击杀 / 最佳用时」，身陨次数实时算；上方可整体改难度、总死亡数；所有改动先进待提交区，点「预览」看清每一行 diff 后才写盘；备份可一键还原。

只监听 `127.0.0.1`，不联网、不上传。检测到 `PBZ.exe` 在跑会直接拒绝写入。

## 用法 B：命令行

```bash
# 1) 先看现状（只读，安全）
python pbz_tool.py info
python pbz_tool.py info --slot ManualSave17

# 2) 改难度（会改所有 20 个自动/手动档，保持一致）
python pbz_tool.py set-difficulty 3

# 3) 改单个 Boss：身陨 0 次 + 最佳用时 35 秒（限逆天者难度的记录）
python pbz_tool.py set-boss WuXuan --diff 3 --deaths 0 --time 0:35.000

# 4) 批量：逆天者难度下所有 Boss 身陨清零
python pbz_tool.py set-boss all --diff 3 --deaths 0

# 5) 总死亡数清零
python pbz_tool.py set-total-deaths 0

# 6) 把入阵者的战报整体改名成逆天者（伪装成一开始就打的最高难度）
python pbz_tool.py move-records --from 1 --to 3

# 7) 想自己抠字段：导出全部槽位 JSON → 改 → 回写
python pbz_tool.py export --out dump/
python pbz_tool.py apply --in dump/
```

先加 `--dry-run` 可以只看会改什么、不落盘。

## 安全设计

- 写盘前自动备份成 `PBZSaveGame.sav.bak-<时间戳>`
- 未修改的槽位**原样复用原始密文块**，一个字节都不动
- 新文件先在 `.tmp` 上重新解析校验（所有块 GCM 认证 + JSON 解析），通过才原子替换
- 检测到 `PBZ.exe` 在跑会拒绝写盘

## 风险 / 注意事项

1. **先完全退出游戏和 Steam**，否则游戏退出时会覆写存档。
2. **关掉 Steam 云同步**（库右键游戏 → 属性 → 通用 → 取消 Steam 云），否则你的改动可能被云端的旧档盖回去；改完进游戏确认正常后再决定要不要开回来。
3. 目录里 `1RealTime / 2Hourly / 3Daily` 是游戏的滚动备份，工具不动它们；需要更彻底的回滚可以直接拿这些文件覆盖。
4. 存档会被重新序列化成紧凑 JSON，所以文件体积会变小（几百 KB），这是正常的，不影响读取。
5. 改难度只对 0~3 有意义；4 = 六十六天是独立槽位概念（`NightmareRun0`），硬塞成 4 可能让档进不去，别乱试。
6. 这游戏没有 EAC/BattlEye，没有服务器校验，修改不影响成就；但**别在开着修改器/内存工具的情况下改档**，容易互相打架。

## 依赖

- Python 3.9+；`cryptography`（AES-GCM）
- 一个 Oodle 解压库 `oo2core_*_win64.dll`。会自动从 Steam 库里找（本机是从 `Death Stranding` 借的），也可写进 `oodle_path.txt` 或用 `--oodle` 指定。
- 重新打包 exe：`python -m PyInstaller --onefile --name "影之刃零-存档工坊" --add-data "web/index.html;web" pbz_web.py`
