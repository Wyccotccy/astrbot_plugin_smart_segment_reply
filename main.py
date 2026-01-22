from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.message.message_event_result import MessageChain
from astrbot.api.message_components import Plain
from astrbot.core.conversation_mgr import Conversation
import aiohttp
import asyncio
import random
import re

@register(
    "astrbot_plugin_smart_segment_reply",
    "Wyccotccy",
    "通过调用硅基流动免费的大模型实现智能的分段回复，也支持自定义分段回复大模型",
    "3.0.4",  # 版本更新
    "https://github.com/Wyccotccy/astrbot_plugin_smart_segment_reply"
)
class SmartSegmentReply(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.siliconflow_key = self.config.get("siliconflow_key", "")
        self.selected_model = self.config.get("model_selection", "THUDM/GLM-4-9B-0414").strip()
        self.api_url = self.config.get("api_url", "https://api.siliconflow.cn/v1/chat/completions")
        self.session = None  # 懒加载ClientSession

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

        try:
            logger.info(f"——模型成功生成回复（原回复：{raw_text}），正在尝试分段回复中——")
            
            # 模型分段+手动兜底（确保至少2段）
            segments = await self.call_model_segment(raw_text)
            if not segments or len(segments) <= 1:
                logger.info("——模型未拆分，启用手动兜底分段——")
                segments = self.manual_segment(raw_text)
                # 强制确保至少2段（短文本也拆分）
                if len(segments) <= 1:
                    segments = self.force_split(segments[0]) if segments else [raw_text]

            logger.info(f"——最终分段结果：{segments}（共{len(segments)}段）——")
            if len(segments) <= 1:
                logger.info("——文本无法拆分，发送原消息——")
                return

            # 禁用默认发送，仅靠同步任务发送（放弃异步，避免中断）
            result.chain.clear()

            # 关键：用同步方式发送（确保循环执行完所有分段，不被中断）
            await self.send_segments_with_history(event, segments)
            
            logger.info(f"——所有分段发送完成——")
        except Exception as e:
            logger.error(f"分段失败，发送原消息，失败原因：{str(e)}")
            result.chain = [Plain(text=raw_text)]
            return

    def manual_segment(self, text: str) -> list[str]:
        """手动按标点拆分（优先保证分段数量）"""
        # 按中文标点拆分（逗号、句号、分号、感叹号、问号）
        split_pattern = r'([，。；！？])'
        parts = re.split(split_pattern, text)
        segments = []
        current = ""
        for part in parts:
            if part in ['，', '。', '；', '！', '？']:
                if current:
                    current += part
                    segments.append(current.strip())
                    current = ""
            else:
                current += part
        if current.strip():
            segments.append(current.strip())
        return [seg for seg in segments if seg]

    def force_split(self, text: str) -> list[str]:
        """终极兜底：文本无法按标点拆分时，强制按长度拆分（确保至少2段）"""
        if len(text) <= 10:
            # 超短文本：按字数拆分（如2-3字一段）
            return [text[i:i+3] for i in range(0, len(text), 3) if text[i:i+3]]
        else:
            # 短文本：从中间拆分
            mid = len(text) // 2
            # 找最近的空格或标点，避免拆分到词中间
            for i in range(mid, len(text)):
                if text[i] in [' ', '，', '。', '；', '！', '？']:
                    mid = i + 1
                    break
            return [text[:mid].strip(), text[mid:].strip()]

    async def send_segments_with_history(self, event: AstrMessageEvent, segments: list[str]):
        """同步发送所有分段（确保循环执行到底），简化历史同步逻辑"""
        umo = event.unified_msg_origin
        conv_mgr = self.context.conversation_manager
        curr_cid = await conv_mgr.get_curr_conversation_id(umo)

        # 遍历所有分段，强制发送（单段异常不中断整体）
        for idx, segment in enumerate(segments):
            try:
                # 简化延迟：固定间隔1.2秒，避免过长/过短
                await asyncio.sleep(1.2)
                
                # 清洗分段（去多余标点）
                clean_segment = re.sub(r'[，,。；;、]+$', '', segment).strip()
                if not clean_segment:
                    logger.warning(f"第{idx+1}段为空，跳过")
                    continue

                # 发送分段（打印日志，确认发送）
                logger.info(f"正在发送第{idx+1}段：{clean_segment}")
                await event.send(MessageChain().message(clean_segment))

                # 简化历史同步：仅在会话存在时同步，失败不影响发送
                if curr_cid:
                    conversation = await conv_mgr.get_conversation(umo, curr_cid)
                    if conversation:
                        new_history = conversation.history.copy()
                        new_history.append({
                            "role": "assistant",
                            "content": clean_segment,
                            "timestamp": event.message_obj.timestamp + idx * 1000
                        })
                        # 历史同步失败仅日志，不中断发送
                        try:
                            await conv_mgr.update_conversation(umo, curr_cid, history=new_history)
                        except Exception as e:
                            logger.warning(f"第{idx+1}段历史同步失败：{str(e)}")
            except Exception as e:
                logger.error(f"第{idx+1}段发送失败：{str(e)}")
                # 单段失败继续发送下一段
                continue

    async def call_model_segment(self, text: str) -> list[str]:
        """简化模型分段，优先保证返回多段"""
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

        # 提示词强化：强制拆分2-4段，示例更明确
        prompt = f"""
强制任务：将以下文本拆分为2-4段，（字数少于8个的可以不拆分）规则：
1. 完整保留原文所有内容，不增删、不修改；
2. 每段1句（短文本），按语义拆分，允许拆分逗号连接的句子；
3. 仅返回分段纯文本，段落间换行，无任何额外内容；

### 原文开始 ###
{text}
### 原文结束 ###
        """
        payload = {
            "model": self.selected_model,
            "messages": [
                {"role": "system", "content": "必须拆分！不拆分则任务失败，严格按示例格式输出"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3,  # 适度随机性，确保拆分
            "max_tokens": 2048,
            "stop": ["\n\n\n"]
        }
        headers = {
            "Authorization": f"Bearer {self.siliconflow_key}",
            "Content-Type": "application/json"
        }

        try:
            async with self.session.post(self.api_url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    resp_text = await resp.text()
                    logger.error(f"API请求失败：{resp.status} - {resp_text}")
                    return []
                data = await resp.json()
                segment_text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                cleaned_segments = [seg.strip() for seg in segment_text.split("\n") if seg.strip()]
                return cleaned_segments
        except Exception as e:
            logger.error(f"模型分段失败：{str(e)}")
            return []

    async def terminate(self):
        if self.session and not self.session.closed:
            await self.session.close()
        logger.info(f"智能分段回复插件已卸载（当前模型：{self.selected_model}），资源已释放")
