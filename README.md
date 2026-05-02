# AI Trader Pro v7.0

高性能多币种自适应加密货币合约交易系统。支持自动交易、AI信号生成、每日策略自动优化（含Walk-Forward验证）。

## 系统架构

```
Binance WebSocket -> 行情数据 -> 技术指标(RSI/MACD/EMA/BB/ATR/Stoch)
                                        |
                                        v
                             OpenRouter AI分析 -> 信号生成
                                        |
                   +--------------------+--------------------+
                   v                    v                    v
             1h趋势过滤           资金费率检查           成交量异常检测
                   |                    |                    |
                   +--------------------+--------------------+
                                        v
                             风控检查(Kelly/熔断/回撤/相关性)
                                        |
                                        v
                             自动下单 -> ATR自适应移动止损
                                        |
                                        v
                             每日Walk-Forward优化 -> 参数更新
```

## 核心功能

| 功能 | 说明 |
|------|------|
| AI信号 | OpenRouter GPT-5.2分析技术指标，输出买卖信号 |
| 自动交易 | 信号置信度>60%自动开仓，支持市价/限价 |
| 多时间框架 | 1h EMA趋势过滤15m信号，减少逆势开仓 |
| 资金费率 | 费率>0.1%阻止开仓，避免被吃费率 |
| Kelly仓位 | 根据历史胜率动态计算最优仓位 |
| 波动率调仓 | 高波动率自动降低仓位，低波动率适度增加 |
| 相关性过滤 | 防止同方向高相关品种过度暴露（如BTC+ETH） |
| 连亏熔断 | 连亏3次暂停30分钟 |
| 回撤限制 | 总回撤>10%停止交易 |
| ATR自适应止损 | 盈利后启动基于ATR的追踪止损，随波动率调整 |
| 实盘回测 | 含滑点、手续费、资金费率、强平模拟 |
| Walk-Forward验证 | 优化后自动做OOS验证，防止过拟合 |
| K线图 | TradingView lightweight-charts，EMA+布林带叠加 |
| 持仓管理 | 实时盈亏、ROE、一键平仓 |
| 手动下单 | 市价/限价、止损止盈、杠杆选择 |
| AI聊天 | 内嵌AI助手，分析行情回答问题 |
| 成交记录 | 完整交易历史，含入场出场盈亏 |
| 系统日志 | 实时查看运行日志 |

## 环境要求

- **操作系统**: Linux (Ubuntu 20.04+) / Windows 10+ / macOS
- **服务器**: 1核1G最低，2核2G推荐
- **Python**: 3.10+
- **网络**: 能访问Binance API和OpenRouter API
- **Binance账户**: 需要开通合约交易，生成API Key（需开启合约权限）
- **OpenRouter账户**: 注册 https://openrouter.ai 获取API Key

## 安装步骤

### Linux 服务器安装

#### 第一步：准备服务器

```bash
# 本地电脑上传文件到VPS
scp ai-trader-pro.zip root@你的服务器IP:~/

# SSH登录服务器
ssh root@你的服务器IP

# 解压
unzip ai-trader-pro.zip
cd ai-trader-pro
```

#### 第二步：安装依赖

```bash
# 给脚本执行权限
chmod +x scripts/*.sh

# 一键安装（Python、pip、venv、依赖、防火墙、systemd）
make install
```

#### 第三步：配置.env

```bash
cp .env.example .env
nano .env
```

必须填写的字段：

```env
BINANCE_API_KEY=你的Binance_API_Key
BINANCE_SECRET=你的Binance_Secret_Key
BINANCE_TESTNET=false
OPENROUTER_API_KEY=sk-or-v1-你的密钥
ACTIVE_MODEL=openai/gpt-5.2
SYMBOLS=BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT
API_KEY=设置一个强密码
AUTO_TRADE=false
```

#### 第四步：启动

```bash
make start       # 前台运行（推荐首次测试）
make daemon      # 后台运行（systemd）
make stop        # 停止
make restart     # 重启
make logs        # 查看日志
make status      # 查看状态
```

#### 第五步：Nginx反代+HTTPS（可选）

```bash
sudo make nginx DOMAIN=你的域名
```

---

### Windows 服务器安装

#### 第一步：安装 Python

1. 下载 Python 3.10+: https://www.python.org/downloads/
2. **安装时务必勾选 "Add Python to PATH"**
3. 打开命令提示符（CMD）或 PowerShell

#### 第二步：解压并安装

```cmd
# 解压 ai-trader-pro.zip 到目标目录
# 进入目录
cd ai-trader-pro

# 运行安装脚本
scripts\install.bat
```

安装脚本会自动：
- 创建 Python 虚拟环境
- 安装所有依赖（ccxt、fastapi、pandas等）
- 创建 data 和 logs 目录
- 从 .env.example 复制 .env 配置文件

#### 第三步：配置.env

```cmd
notepad .env
```

填写你的 API 密钥（同 Linux 步骤）。

#### 第四步：启动

```cmd
scripts\start.bat
```

浏览器打开: `http://localhost:8000?key=你设置的API_KEY`

#### 第五步（可选）：开机自启动

以管理员身份运行：

```cmd
scripts\install-service.bat
```

这会创建 Windows 计划任务，系统启动时自动运行。

#### Windows 防火墙

如果需要外部访问，开放 8000 端口：

```powershell
# PowerShell（管理员）
New-NetFirewallRule -DisplayName "AI Trader" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
```

#### Windows 生产部署建议

| 项目 | 建议 |
|------|------|
| 反向代理 | 使用 IIS 或 Caddy 代理 HTTPS |
| 进程管理 | 使用 install-service.bat 创建计划任务 |
| 日志 | 查看 logs\\ai-trader.log |
| 自动重启 | 计划任务已配置失败自动重启 |

---

## 使用流程

### 测试阶段（第1-2周）

```env
AUTO_TRADE=false
BINANCE_TESTNET=true
```

1. 启动后观察右侧面板的AI信号
2. 检查信号方向是否合理
3. 查看置信度，大部分应>60%
4. 使用 `/api/walk_forward` 验证策略OOS表现
5. 手动在面板下单测试
6. 确认持仓、盈亏、平仓功能正常

### 实盘阶段（确认无误后）

```env
AUTO_TRADE=false
BINANCE_TESTNET=false
```

1. 先手动交易几笔，确认API权限正常
2. 观察信号质量1-2天
3. 没问题后开启自动交易

### 开启自动交易

```env
AUTO_TRADE=true
```

然后重启：`make restart`（Linux）或 `scripts\start.bat`（Windows）

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/status` | GET | 系统状态 |
| `/api/positions` | GET | 当前持仓 |
| `/api/order` | POST | 下单 |
| `/api/close` | POST | 平仓 |
| `/api/signal/{symbol}` | GET | 获取信号 |
| `/api/backtest` | POST | 回测（含滑点/费率/强平） |
| `/api/walk_forward` | POST | Walk-Forward验证 |
| `/api/optimize` | POST | 参数优化 |
| `/api/chat` | POST | AI聊天 |
| `/api/risk` | GET | 风控状态 |
| `/api/indicators/{symbol}` | GET | 技术指标 |
| `/api/candles/{symbol}` | GET | K线数据 |
| `/api/trades` | GET | 交易记录 |
| `/api/equity` | GET | 权益曲线 |
| `/api/auto_trade` | POST | 开关自动交易 |

## Web面板功能

| 区域 | 功能 |
|------|------|
| 左侧-币种切换 | 点击切换BTC/ETH/SOL等，显示实时价格和涨跌幅 |
| 左侧-风险状态 | 权益、日盈亏、回撤、胜率、连亏次数、波动率 |
| 左侧-资金曲线 | 7天权益变化面积图 |
| 左侧-快速下单 | 选择币种/方向/类型/数量/止损止盈/杠杆，一键下单 |
| 中间-K线图 | TradingView实时蜡烛图，叠加EMA9/EMA21/布林带 |
| 中间-指标条 | RSI、MACD、BB%、成交量、EMA、ATR、随机指标 |
| 中间-底部标签 | 持仓管理/成交记录/系统日志/AI聊天 |
| 右侧-AI信号 | 实时信号卡片，含置信度进度条和"跟单"按钮 |
| 右侧-S/R支撑阻力 | 自动计算的支撑位和阻力位 |
| 顶栏-自动交易 | 点击AUTO:OFF切换为自动交易模式 |

## 常用命令

### Linux

```bash
make start            # 前台启动
make daemon           # 后台启动（systemd）
make stop             # 停止
make restart          # 重启
make logs             # 查看实时日志
make status           # 查看运行状态
make nginx DOMAIN=x   # 配置Nginx+SSL
```

### Windows

```cmd
scripts\start.bat              # 启动
scripts\install-service.bat    # 安装为开机自启（管理员）
# 查看日志: 打开 logs\\ai-trader.log
# 停止: Ctrl+C 或 schtasks /end /tn "AITraderPro"
```

## 日志位置

```bash
# Linux
tail -f logs/ai-trader.log
journalctl -u ai-trader -f

# Windows
# 打开 logs\\ai-trader.log
# 或 PowerShell: Get-Content logs\\ai-trader.log -Wait
```

## 故障排查

| 问题 | 解决 |
|------|------|
| 启动报错"API key missing" | 检查.env中OPENROUTER_API_KEY是否填写 |
| 启动报错"Exchange init failed" | 检查BINANCE_API_KEY和BINANCE_SECRET |
| K线图不显示 | 检查浏览器控制台，确认WebSocket连接正常 |
| 不出信号 | 检查日志，可能在冷却期或数据不足（需50根K线） |
| 自动不下单 | 确认AUTO_TRADE=true，检查日志中的拒绝原因 |
| 限价单不成交 | 正常现象，120秒超时会自动取消 |
| 回撤过大停机 | 等第二天自动重置，或重启进程 |
| Walk-Forward显示过拟合 | 系统会自动拒绝过拟合参数，无需手动干预 |
| Windows Python找不到 | 重新安装Python并勾选"Add to PATH" |

## 安全建议

1. **API Key权限**: 只开合约交易权限，不开提币权限
2. **IP白名单**: Binance API设置中限制服务器IP
3. **强密码**: .env中API_KEY设置复杂密码
4. **先测试网**: BINANCE_TESTNET=true先测试
5. **小仓位**: 初始杠杆5x，风险每笔1-2%
6. **监控**: 配置Telegram通知，及时收到开仓/平仓消息
7. **Walk-Forward**: 每次优化后检查OOS表现，拒绝过拟合参数

## 文件结构

```
ai-trader-pro/
├── .env                    # 配置文件（从.env.example复制）
├── Makefile                # 快捷命令（Linux）
├── requirements.txt        # Python依赖
├── app/
│   ├── config.py           # 配置加载
│   ├── models.py           # 数据模型
│   ├── database.py         # SQLite数据库
│   ├── exchange.py         # Binance API（ccxt）
│   ├── data_feed.py        # WebSocket行情
│   ├── indicators.py       # 技术指标计算
│   ├── strategy.py         # AI信号生成 + 信号准确率追踪
│   ├── risk_manager.py     # 风控管理（Kelly/相关性/波动率调仓）
│   ├── order_manager.py    # 订单管理 + ATR自适应止损
│   ├── backtester.py       # 回测引擎（滑点/费率/强平/Walk-Forward）
│   ├── optimizer.py        # 参数优化 + Walk-Forward验证
│   ├── auto_optimizer.py   # 每日自动优化（含OOS验证）
│   ├── notifier.py         # Telegram通知
│   └── main.py             # FastAPI主程序
├── static/
│   └── index.html          # Web前端
├── scripts/
│   ├── install.sh          # Linux安装脚本
│   ├── install.bat         # Windows安装脚本
│   ├── start.sh            # Linux启动脚本
│   ├── start.bat           # Windows启动脚本
│   ├── stop.sh             # Linux停止脚本
│   ├── install-service.bat # Windows开机自启安装
│   └── setup_nginx.sh      # Nginx配置脚本
├── systemd/
│   └── ai-trader.service   # systemd服务文件（Linux）
├── data/                   # SQLite数据库（自动创建）
└── logs/                   # 日志目录（自动创建）
```

## 免责声明

本系统仅供学习和研究使用。加密货币合约交易风险极高，可能导致全部本金亏损。请在充分了解风险的前提下使用，作者不对任何交易损失负责。
