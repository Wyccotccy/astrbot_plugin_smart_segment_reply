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
import json

@register(
    "astrbot_plugin_smart_segment_reply",
    "Wyccotccy",
    "通过调用硅基流动免费的大模型实现智能的分段回复，也支持自定义分段回复大模型",
    "3.0.7",
    "https://github.com/Wyccotccy/astrbot_plugin_smart_segment_reply"
)
class SmartSegmentReply(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.siliconflow_key = self.config.get("siliconflow_key", "")
        self.selected_model = self.config.get("model_selection", "THUDM/GLM-4-9B-0414").strip()
        self.api_url = self.config.get("api_url", "https://api.siliconflow.cn/v1/chat/completions")
        self.session = None

    @filter.on_decorating_result()
    async def handle_segment_reply(self, event: AstrMessageEvent):
        if not self.siliconflow_key:
            logger.warning("未配置硅基流动API Key，跳过分段回复")
            return

        result = event.get_result()
        if not result or not result.chain:
            return

        raw_text = ""
        for comp in result.chain:
            if isinstance(comp, Plain):
                raw_text += comp.text.strip()
        raw_text = raw_text.strip()
        if not raw_text:
            return

        try:
            logger.info(f"——模型生成回复（原回复：{raw_text}），尝试分段——")
            
            segments = await self.call_model_segment(raw_text)
            if not segments or len(segments) <= 1:
                logger.info("——模型未拆分，启用手动分段——")
                segments = self.manual_segment(raw_text)
                if len(segments) <= 1:
                    segments = self.force_split(segments[0]) if segments else [raw_text]

            logger.info(f"——最终分段：{segments}（共{len(segments)}段）——")
            if len(segments) <= 1:
                logger.info("——文本无需拆分，发送原消息——")
                return

            result.chain.clear()
            await self.send_segments_with_history(event, segments)
            logger.info("——所有分段发送完成——")
        except Exception as e:
            logger.error(f"分段失败：{str(e)}，发送原消息")
            result.chain = [Plain(text=raw_text)]
            return

    def manual_segment(self, text: str) -> list[str]:
        # 按中文标点拆分，保留语义连贯
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
        # 文本无标点时，按长度兜底拆分
        if len(text) <= 10:
            return [text[i:i+3] for i in range(0, len(text), 3) if text[i:i+3]]
        else:
            mid = len(text) // 2
            for i in range(mid, len(text)):
                if text[i] in [' ', '，', '。', '；', '！', '？']:
                    mid = i + 1
                    break
            return [text[:mid].strip(), text[mid:].strip()]

    async def send_segments_with_history(self, event: AstrMessageEvent, segments: list[str]):
        umo = event.unified_msg_origin
        conv_mgr = self.context.conversation_manager
        curr_cid = await conv_mgr.get_curr_conversation_id(umo)

        for idx, segment in enumerate(segments):
            try:
                await asyncio.sleep(1.2)
                
                clean_segment = re.sub(r'[，,。；;、]+$', '', segment).strip()
                if not clean_segment:
                    logger.warning(f"第{idx+1}段为空，跳过")
                    continue

                logger.info(f"发送第{idx+1}段：{clean_segment}")
                await event.send(MessageChain().message(clean_segment))

                if curr_cid:
                    conversation = await conv_mgr.get_conversation(umo, curr_cid)
                    if conversation:
                        # 兼容历史格式（字符串/列表）
                        if isinstance(conversation.history, str):
                            try:
                                history_list = json.loads(conversation.history)
                                if not isinstance(history_list, list):
                                    history_list = []
                            except:
                                history_list = []
                        else:
                            history_list = conversation.history.copy() if isinstance(conversation.history, list) else []

                        history_list.append({
                            "role": "assistant",
                            "content": clean_segment,
                            "timestamp": event.message_obj.timestamp + idx * 1000
                        })

                        try:
                            await conv_mgr.update_conversation(
                                unified_msg_origin=umo,
                                conversation_id=curr_cid,
                                history=history_list
                            )
                        except Exception as e:
                            logger.warning(f"第{idx+1}段历史同步失败：{str(e)}")
            except Exception as e:
                logger.error(f"第{idx+1}段发送失败：{str(e)}")
                continue

    async def call_model_segment(self, text: str) -> list[str]:
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

        prompt = """
任务：将以下文本进行分段处理，规则如下：
1. 完整保留原文所有内容，不增删、不修改任何信息；
2. 按原文语义和标点（逗号、句号）断点拆分，长文本可拆分为2-4段，每段1-2句；
3. 短文本（少于10字）可不分段，直接返回原文；
4. 仅返回分段纯文本，段落间用换行分隔，禁止添加任何序号、标记或额外说明。

### 原文开始 ###
{text}
### 原文结束 ###
        """.format(text=text)

        payload = {
            "model": self.selected_model,
            "messages": [
                {"role": "system", "content": "按规则灵活处理分段，短文本可不分段，仅输出纯净分段结果"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3,
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
        logger.info(f"智能分段回复插件已卸载（当前模型：{self.selected_model}）")
