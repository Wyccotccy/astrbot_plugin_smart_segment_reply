from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.message.message_event_result import MessageChain
from astrbot.api.message_components import Plain
import aiohttp
import asyncio
import random
import re
import json

@register(
    "astrbot_plugin_smart_segment_reply",
    "Wyccotccy",
    "通过调用硅基流动免费的大模型实现智能的分段回复，也支持自定义分段回复大模型",
    "3.0.2",
    "https://github.com/Wyccotccy/astrbot_plugin_smart_segment_reply"
)
class SmartSegmentReply(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.siliconflow_key = self.config.get("siliconflow_key", "")
        self.selected_model = self.config.get("model_selection", "THUDM/GLM-4-9B-0414").strip()
        self.api_url = self.config.get("api_url", "https://api.siliconflow.cn/v1/chat/completions")
        self.exclude_keywords = self.config.get("exclude_keywords", [])
        # 获取随机延迟范围，默认 [1, 3] 秒
        delay_range = self.config.get("random_delay_range", [1, 3])
        if isinstance(delay_range, list) and len(delay_range) >= 2:
            self.delay_min = float(delay_range[0])
            self.delay_max = float(delay_range[1])
        else:
            self.delay_min = 1.0
            self.delay_max = 3.0
        self.session = None

    @filter.on_decorating_result()
    async def handle_segment_reply(self, event: AstrMessageEvent):
        if not self.siliconflow_key:
            logger.warning("未配置硅基流动API Key，跳过分段回复")
            return

        result = event.get_result()
        if not result or not result.chain:
            return

        # 提取原始文本（按顺序拼接，保留所有内容）
        raw_text = ""
        for comp in result.chain:
            if isinstance(comp, Plain):
                raw_text += comp.text.strip()
        raw_text = raw_text.strip()
        if not raw_text:
            return

        # 检查是否包含排除关键词（不区分大小写）
        if self.exclude_keywords:
            text_lower = raw_text.lower()
            for keyword in self.exclude_keywords:
                if keyword and keyword.lower() in text_lower:
                    logger.info(f"检测到排除关键词 '{keyword}'，跳过分段处理")
                    return

        try:
            logger.info(f"——模型成功生成回复（原回复：{raw_text}），正在尝试分段回复中——")
            
            segments = await self.call_model_segment(raw_text)
            if not segments or len(segments) <= 1:
                logger.info(f"——分段回复成功，共分1段（无需拆分）——")
                return

            # 保存分段后的完整文本，用于后续保存到聊天记录
            full_segmented_text = "\n\n".join(segments)
            
            # 清空原消息链，避免 AstrBot 后续重复发送
            result.chain.clear()
            
            # 分段延迟发送
            for i, segment in enumerate(segments):
                if i > 0:  # 第一段不延迟，后续段延迟
                    delay = random.uniform(self.delay_min, self.delay_max)
                    await asyncio.sleep(delay)
                await event.send(MessageChain().message(segment))
            
            # 关键修复：手动保存到对话历史（包含用户输入和助手回复）
            await self._save_to_conversation_history(event, full_segmented_text)
            
            logger.info(f"——分段回复成功，共分{len(segments)}段——")
            
        except Exception as e:
            logger.error(f"分段失败，发送原消息，失败原因：{str(e)}")
            # 发生异常时不修改result，让原消息正常发送
            return

    async def _save_to_conversation_history(self, event: AstrMessageEvent, content: str):
        """手动保存分段后的内容到对话历史（包含用户输入和助手回复）"""
        try:
            conv_mgr = self.context.conversation_manager
            if not conv_mgr:
                return
            
            umo = event.unified_msg_origin
            curr_cid = await conv_mgr.get_curr_conversation_id(umo)
            
            if curr_cid:
                conversation = await conv_mgr.get_conversation(umo, curr_cid)
                if conversation:
                    try:
                        history = json.loads(conversation.history) if isinstance(conversation.history, str) else conversation.history
                    except:
                        history = []
                    
                    # 修复：确保用户输入被记录到历史
                    user_content = event.message_str
                    if user_content:
                        # 检查历史记录是否已包含本次用户输入（避免重复）
                        if not history or history[-1].get("role") != "user":
                            history.append({
                                "role": "user",
                                "content": user_content
                            })
                    
                    # 添加助手回复（分段后的内容）
                    history.append({
                        "role": "assistant",
                        "content": content
                    })
                    
                    await conv_mgr.update_conversation(
                        unified_msg_origin=umo,
                        conversation_id=curr_cid,
                        history=history
                    )
                    logger.debug(f"已保存对话历史（含用户输入和分段回复）到对话ID: {curr_cid}")
        except Exception as e:
            logger.error(f"保存对话历史失败: {str(e)}")

    async def call_model_segment(self, text: str) -> list[str]:
        # 懒加载ClientSession（避免Loop未初始化问题）
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

        # 安全提示词（用分隔符避免注入，保留所有原文内容）
        prompt = f"""
任务：将以下文本拆分为3-5个自然段落，严格遵循以下规则：
1. 完整保留原文所有内容（包括重复内容），不增删、不修改任何信息；
2. 按原文语义和标点断点拆分，保持上下文逻辑连贯；
3. 每段1-3句话，长度适中；
4. 仅返回分段纯文本，段落间换行分隔，禁止加序号/标记/额外内容。

### 原文开始 ###
{text}
### 原文结束 ###
        """
        payload = {
            "model": self.selected_model,
            "messages": [
                {"role": "system", "content": "仅执行文本分段任务，严格遵守规则，保留所有原文内容"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
            "max_tokens": 2048,
            "stop": ["\n\n\n"]
        }
        headers = {
            "Authorization": f"Bearer {self.siliconflow_key}",
            "Content-Type": "application/json"
        }

        async with self.session.post(self.api_url, json=payload, headers=headers) as resp:
            if resp.status != 200:
                resp_text = await resp.text()
                raise Exception(f"API请求失败（状态码{resp.status}）：{resp_text}")
            data = await resp.json()
            segment_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            # 清洗分段结果（去掉序号/标记，过滤空行）
            cleaned_segments = []
            for seg in segment_text.split("\n"):
                seg = seg.strip()
                if seg:
                    # 移除开头的序号（数字、点、空格等）
                    cleaned_seg = re.sub(r'^[\d\.\s、，()【】]*', '', seg).strip()
                    if cleaned_seg:
                        cleaned_segments.append(cleaned_seg)
            return cleaned_segments

    async def terminate(self):
        # 安全关闭ClientSession
        if self.session and not self.session.closed:
            await self.session.close()
        logger.info(f"智能分段回复插件已卸载（当前模型：{self.selected_model}），资源已释放")
