# Xvfb + 自动点击方案 - 绕过 Stripe hCaptcha

## 方案概述

在服务器/无头环境下，通过 **Xvfb 虚拟显示 + SwiftShader 软件 GPU + 自动点击 hCaptcha checkbox** 实现 Stripe 支付时 hCaptcha 自动通过。

**成功率**: 4/4 (100%) 测试全部通过  
**平均耗时**: ~5-9 秒（从 handleNextAction 开始到完成）

## 原理

### hCaptcha 双层检测

Stripe 使用两层 hCaptcha：

1. **Invisible hCaptcha** (sitekey: `463b917e-...`)
   - 页面加载时自动运行
   - 检查浏览器指纹（GPU、Canvas、WebGL 等）
   - 真实显示 + GPU → 自动通过
   - Xvfb/Headless → **失败**

2. **Visible Checkbox** (sitekey: `c7faac4c-...`)
   - 仅当 Invisible 检测失败时出现
   - 在 `handleNextAction()` 期间创建
   - 嵌套在深层 iframe 中：
     ```
     checkout.stripe.com
     └─ js.stripe.com/v3/hcaptcha-inner-*.html
        └─ b.stripecdn.com/HCaptcha.html
           └─ newassets.hcaptcha.com/#frame=checkbox  ← 这里有 checkbox
           └─ newassets.hcaptcha.com/#frame=challenge
     ```

### 为什么方案有效

- **SwiftShader** 让 Chrome 有软件 WebGL，浏览器指纹更接近真实
- **Xvfb 有头模式** 没有 `HeadlessChrome` UA 标记
- **自动点击** checkbox 在 invisible 检测失败后的备用验证中通过
- CDP 连接方式 (`connect_over_cdp`) 让 `navigator.webdriver = false`

## 环境要求

```bash
# 安装 Xvfb
sudo apt-get install -y xvfb

# Chrome for Testing (Playwright 自带)
~/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome

# Python 依赖
pip install playwright
```

## 使用方法

### 1. 启动 Xvfb

```bash
# 启动虚拟显示 (1920x1080, 24位色深)
Xvfb :99 -screen 0 1920x1080x24 -ac &

# 验证
DISPLAY=:99 xdpyinfo | head -5
```

### 2. 运行支付测试

```bash
# 在 Xvfb 上运行 (不加 --headless，以有头模式运行)
DISPLAY=:99 python3 test_browser_payment.py

# 对比：纯 headless（会被 hCaptcha 检测）
python3 test_browser_payment.py --headless
```

### 3. 集成到自动化

```python
from browser_payment import BrowserPayment

bp = BrowserPayment(
    proxy="http://proxy:port",
    headless=False,  # 重要：不用 headless，用 Xvfb 代替
    slow_mo=80,
)

# 确保 DISPLAY=:99（Xvfb）
result = bp.run_full_flow(
    session_token=...,
    access_token=...,
    card_number=...,
    # ...
)
```

## Chrome 启动参数

关键参数（已集成到 `browser_payment.py`）：

```
--use-gl=angle                    # 使用 ANGLE GL
--use-angle=swiftshader-webgl     # SwiftShader WebGL
--enable-unsafe-swiftshader       # 允许 SwiftShader
--no-sandbox                      # 容器/WSL 环境
--remote-debugging-port=PORT      # CDP 连接
```

**不要使用**：
- `--headless=new` — 会被 hCaptcha 检测 UA 中的 HeadlessChrome
- `--disable-gpu` — 会禁用 WebGL，降低浏览器指纹质量

## 自动点击实现

`_try_click_hcaptcha()` 使用 Playwright 的 `page.frames` 遍历所有嵌套 iframe：

```python
for frame in page.frames:
    if "newassets.hcaptcha.com" in frame.url and "frame=checkbox&" in frame.url:
        checkbox = frame.query_selector('#checkbox')
        checkbox.click()
```

监控循环每 0.5 秒检查一次，通常在 handleNextAction 启动后 2 秒内检测到 checkbox。

## 测试结果

| 环境 | hCaptcha 结果 | 说明 |
|------|-------------|------|
| DISPLAY=:0 (真实显示) | 需手动点击 | Invisible 检测可能通过，但不稳定 |
| DISPLAY=:99 (Xvfb + SwiftShader) | **自动通过** | Invisible 失败 → checkbox 自动点击 |
| `--headless=new` | 超时 | 被 HeadlessChrome UA 检测 |
| `--headless=new` + UA 覆盖 | 超时 | hCaptcha 仍可通过其他方式检测 |
| YesCaptcha 打码 | 失败 | Enterprise hCaptcha IP 绑定，token 被拒 |

## 故障排除

### checkbox 未被检测到
- 确认 `handleNextAction` 在运行（不能 skip）
- 检查 `page.frames` 中是否有 `newassets.hcaptcha.com` URL
- 确认超时足够长（当前 60s）

### Chrome 启动失败
- 检查 Xvfb 是否运行: `pgrep -f 'Xvfb :99'`
- 检查 DISPLAY 环境变量: `echo $DISPLAY`
- Chrome 145 路径: `~/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome`

### SwiftShader 警告
```
Automatic fallback to software WebGL has been deprecated.
Please use the --enable-unsafe-swiftshader flag
```
已通过 `--enable-unsafe-swiftshader` 解决。

## 生产部署

```bash
# systemd service 示例
[Service]
Environment=DISPLAY=:99
ExecStartPre=/usr/bin/Xvfb :99 -screen 0 1920x1080x24 -ac
ExecStart=/usr/bin/python3 /path/to/main.py
```

或 Docker:
```dockerfile
RUN apt-get install -y xvfb
CMD Xvfb :99 -screen 0 1920x1080x24 -ac & DISPLAY=:99 python3 main.py
```
