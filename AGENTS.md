# 仓库规则（patent-abnormal-screening-skill）

> 给所有在这个仓库里干活的 AI：改完就提交并推送，署名必须是仓库主人本人。

## 改完直接提交推送，不用问

改完 `skill/` 下任何文件，主动 `commit` + `push origin main`，不要停在"改好了"就结束。推完回一句：commit sha、几个文件、本地与远端是否同步。

提交信息：中文一句话标题 + 3–6 条要点，只写"这次改了什么"，不写版本沿革（不写"v2 相比 v1 改了什么"、不写"原…改为…"）。

## 提交人必须是仓库主人，不能出现 AI

- `user.name = arvinooo`、`user.email = 2943781161@qq.com`；没设局部身份就用全局，**不许临时改身份**
- 不许 `git commit --author` 换成别的名字
- 不许加 `Co-Authored-By: Claude / DeepSeek / DSH / AI` 这类尾注，也不许在提交信息里署 AI 的名
- 提交后自检：`git log -1 --format='%an <%ae> | %cn <%ce>'` 必须全是 `arvinooo <2943781161@qq.com>`

## 硬约束

- 不许 `git push --force`；远端有新提交一律 `git pull --rebase` 上去，冲突保留工作区里的最新内容
- 不许把凭据（`发明人key.txt`、企查查 key、mindflow JWT）、客户文本、`cache/`、`__pycache__` 提交进库（`.gitignore` 已排除 cache/ 与字节码）
- 推送失败（网络、鉴权、冲突解不了）就明确说原因，别静默跳过
