# -*- coding: utf-8 -*-
"""AI 回复：DeepSeek 生成私信回复草稿。

流程：新消息 → 组装上下文（业务设定+对话历史+新消息）→ DeepSeek 生成 →
返回草稿。发送始终由用户确认（点发送按钮），或在设置中开启全自动。
"""
import json
import urllib.request
import urllib.error

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
TIMEOUT = 30


def generate_reply(api_key, business_context, history, new_message,
                   nickname=""):
    """生成回复草稿。

    api_key: DeepSeek API Key
    business_context: 业务设定（做什么业务、怎么称呼、禁忌等）
    history: [(role, text)]，role 为 'user'/'assistant'，最近的对话
    new_message: 最新客户消息
    nickname: 客户昵称（可选）

    返回 (reply_text, error)。成功时 error 为 None。
    """
    if not (api_key or "").strip():
        return "", "未配置 DeepSeek API Key（去设置里填）"
    if not (new_message or "").strip():
        return "", "消息为空"

    sys_prompt = (
        "你是小红书商家客服，用简洁、亲切的中文回复客户私信。\n"
        f"业务背景：{business_context or '（未设置）'}\n"
        "规则：\n"
        "1. 只回答业务相关问题，不确定的说'我帮您问下'别瞎编\n"
        "2. 别提你是 AI\n"
        "3. 简短，3 句话以内\n"
        "4. 不要表情包堆砌，最多 1 个表情\n"
    )
    messages = [{"role": "system", "content": sys_prompt}]
    for role, text in (history or [])[-10:]:
        if text and text.strip():
            messages.append({"role": role, "content": text.strip()[:500]})
    who = f"（客户昵称：{nickname}）" if nickname else ""
    messages.append({"role": "user",
                     "content": f"{who}客户新消息：{new_message.strip()}"})

    body = json.dumps({
        "model": MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 300,
    }).encode("utf-8")
    req = urllib.request.Request(
        API_URL, data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key.strip()}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8"))
            msg = detail.get("message") or detail.get("error", {}).get(
                "message", "")
        except Exception:
            msg = ""
        return "", f"DeepSeek 接口错误 {e.code}：{msg or '检查 Key/余额'}"
    except Exception as e:
        return "", f"请求失败：{e.__class__.__name__}: {e}"
    try:
        reply = data["choices"][0]["message"]["content"].strip()
        return reply, None
    except Exception:
        return "", "接口返回格式异常"


def test_connection(api_key):
    """测试 Key 是否可用。返回 (True/False, 说明)。"""
    reply, err = generate_reply(api_key, "测试", [], "你好")
    if err:
        return False, err
    return True, f"连接正常，测试回复：{reply[:50]}"
