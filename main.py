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
import json  # 导入JSON解析模块

@register(
    "astrbot_plugin_smart_segment_reply",
    "Wyccotccy",
    "通过调用硅基流动免费的大模型实现智能的分段回复，也支持自定义分段回复大模型",
    "3.0.5",  # 版本更新
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
            if not segments or 错误定位：对话历史格式异常
核心原因：`conversation.history` 实际是 **字符串类型**（而非预期的列表），调用 `copy()` 方法报错。可能是对话历史存储格式不一致（如JSON字符串未解析）。

### 修复代码（兼容历史格式，确保发送成功）
```python
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
import json  # 导入JSON解析模块

@register(
    "astrbot_plugin_smart_segment_reply",
    "Wyccotccy",
    "通过调用硅基流动免费的大模型实现智能的分段回复，也支持自定义分段回复大模型",
    "3.0.5",  # 版本更新
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

            # 禁用默认发送，仅靠同步任务发送
            result.chain.clear()

            # 同步发送所有分段
            await self.send_segments_with_history(event, segments)
            
            logger.info(f"——所有分段发送完成——")
        except Exception as e:
            logger.error(f"分段失败，发送原消息，失败原因：{str(e)}")
            result.chain = [Plain(text=raw_text)]
            return

    def manual_segment(self, text: str) -> list[str]:
        """手动按标点拆分（优先保证分段数量）"""
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
            return [text[i:i+3] for i in range(0, len(text), 3) if text[i:i+3]]
        else:
            mid = len(text) // 2
            for i in range(mid, len(text)):
                if text[i] in [' ', '，', '。', '；', '！', '？']:
                    mid = i + 1
                    break
            return [text[:mid].strip(), text[mid:].strip()]

    async def send_segments_with_history(self, event: AstrMessageEvent, segments: list[str]):
        """同步发送所有分段，兼容对话历史格式（字符串/列表）"""
        umo = event.unified_msg_origin
        conv_mgr = self.context.conversation_manager
        curr_cid = await conv_mgr.get_curr_conversation_id(umo)

        # 遍历所有分段，强制发送（单段异常不中断）
        for idx, segment in enumerate(segments):
            try:
                await asyncio.sleep(1.2)  # 固定间隔
                
                # 清洗分段
                clean_segment = re.sub(r'[，,。；;、]+$', '', segment).strip()
                if not clean_segment:
                    logger.warning(f"第{idx+1}段为空，跳过")
                    continue

                # 核心：先发送消息（不受历史同步影响）
                logger.info(f"正在发送第{idx+1}段：{clean_segment}")
                await event.send(MessageChain().message(clean_segment))

                # 兼容历史格式：处理conversation.history可能是字符串的情况
                if curr_cid:
                    conversation = await conv_mgr.get_conversation(umo, curr_cid)
                    if conversation:
                        # 关键修复：解析历史（字符串→列表）
                        if isinstance(conversation.history, str):
                            try:
                                # 尝试解析JSON字符串为列表
                                history_list = json.loads(conversation.history)
                                if not isinstance(history_list, list):
                                    history_list = []
                            except:
                                # 解析失败则初始化空列表
                                history_list = []
                        else:
                            # 本身是列表则直接使用
                            history_list = conversation.history.copy() if isinstance(conversation.history, list) else []

                        # 追加分段历史
                        history_list.append({
                            "role": "assistant",
                            "content": clean_segment,
                            "timestamp": event.message_obj.timestamp + idx * 1000
                        })

                        # 更新对话历史
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
        """简化模型分段，优先保证返回多段"""
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

        prompt = f"""
强制任务：将以下文本拆分为2-4段，规则：
1. 完整保留原文所有内容，不增删、不修改；
2. 每段1句（短文本），按语义拆分，允许拆分逗号连接的句子；
3. 仅返回分段纯文本，段落间换行，无任何额外内容；
4. 示例：原文“日照香炉生紫烟，遥看瀑布挂前川。飞流直下三千尺，疑是银河落九天。”
   拆分为：
   日照香炉生紫烟，
   遥看瀑布挂前川。
   飞流直下三千尺，
   疑是银河落九天。
原文：哈哈哈你想干点什么呢？我好无聊
   拆分为:
   哈哈哈你想干点什么呢？
   我好无聊
### 原文开始 ###
{text}
### 原文结束 ###
        """
        payload = {
            "model": self.selected_model,
            "messages": [
                {"role": "system", "content": "不拆分则任务失败，严格按示例格式输出"},
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
        logger.info(f"智能分段回复插件已卸载（当前模型：{self.selected_model}），资源已释放")
