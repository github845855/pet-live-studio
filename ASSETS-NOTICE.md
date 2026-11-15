# 素材与授权说明

- **代码**：MIT，见 `LICENSE`。
- **动作片段 `webm/` 与角色形象**：来源未确认。

这批素材移植自第三方桌面宠物项目 `dsh-pet-main`。该项目目录中**没有附带
LICENSE 文件**，角色形象（蓝发女仆）的著作权归属也无法从仓库判断。

因此：

| 用途 | 建议 |
|---|---|
| 自己本地玩、内部测试 | 没有问题 |
| 公开分发、商用、公开直播 | 先确认授权，或替换成自己的素材 |

**替换素材**：把新片段（约 10 秒、640×360、VP9-alpha 的 webm）放进 `webm/`，
再运行：

```bash
python tools/build_action_catalog.py
```

索引会重新生成。动作名即文件名；要参与随机动作，在
`tools/build_action_catalog.py` 的分类列表里加一行。

B 站弹幕依赖 blivedm（MIT），见 `vendor/NOTICE.txt`。
抖音接入依赖外部 DyHub，本项目不打包其代码。
