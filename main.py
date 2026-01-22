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
    "3.0.2",  # 版本更新
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
            
            segments = await self.call_model_segment(raw_text)
            if not segments or len(segments) <= 1:
                logger.info(f"——分段回复成功，共分1段（无需拆分）——")
                return

            # 关键修复：清空result.chain，禁用默认发送逻辑（避免重复）
            result.chain.clear()

            # 仅通过异步任务发送分段消息，同时同步历史
            问题定位：重复发送的核心原因
修复后代码存在 **双重发送逻辑**，导致消息重复：
1. `result.chain = [Plain(text=merged_segments)]` 会触发 AstrBot 默认发送流程，推送一次合并后的完整回复；
2. `asyncio.create_task(send_segments_with_history(...))` 又主动发送了一次分段消息，导致重复。

用户看到的“日照香炉生紫烟,” 是分段发送的第一句，而完整四句是默认流程发送的合并版，因此出现两次消息。

### 最终修复代码（彻底解决重复）
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

@register(
    "astrbot_plugin_smart_segment_reply",
    "Wyccotccy",
    "通过调用硅基流动免费的大模型实现智能的分段回复，也支持自定义分段回复大模型",
    "3.0.2",  # 版本更新
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
            
            segments = await self.call_model_segment(raw_text)
            if not segments or len(segments) <= 1:
                logger.info(f"——分段回复成功，共分1段（无需拆分）——")
                return

            # 关键修复：清空result.chain，禁用默认发送逻辑（避免重复）
            result.chain.clear()

            # 仅通过异步任务发送分段消息，同时同步历史
            asyncio.create_task(self.send_segments_with_history(event, segments))
            
            logger.info(f"——分段回复任务已启动，共分{len(segments)}段——")
        except Exception as e:
            logger.error(f"分段失败，发送原消息，失败原因：{str(e)}")
            # 异常时恢复原消息链，确保正常发送
            result.chain = [Plain(text=raw_text)]
            return

    async def send_segments_with_history(self, event: AstrMessageEvent, segments: list[str]):
        """延迟发送分段消息，并手动同步到对话历史（唯一发送渠道）"""
        umo = event.unified_msg_origin  # 会话唯一标识
        conv_mgr = self.context.conversation_manager  # 对话管理器
        curr_cid = await conv_mgr.get_curr_conversation_id(umo)  # 当前对话ID

        # 遍历分段，延迟发送并同步历史
        for idx, segment in enumerate(segments):
            # 避免首段发送过快，优化体验
            await asyncio.sleep(random.uniform(1.5, 3) if idx == 0 else random.uniform(1, 2))
            
            # 1. 发送分段消息（唯一发送逻辑）
            segment = segment.rstrip("，,")  # 去除末尾多余标点（解决用户反馈的“紫烟,”问题）
            message_chain = MessageChain().message(segment)
            await event.send(message_chain)
            
            # 2. 手动同步到对话历史（确保记录不缺失）
            if not curr_cid:
                continue
            conversation = await conv_mgr.get_conversation(umo, curr_cid)
            if not conversation:
                logger.warning(f"无法获取对话[{curr_cid}]，跳过该分段历史记录")
                continue
            
            # 构建历史条目（与LLM对话格式一致）
            history_entry = {
                "role": "assistant",
                "content": segment,
                "timestamp": event.message_obj.timestamp + idx  # 避免时间戳重复
            }
            
            # 更新对话历史（追加分段，不覆盖原有内容）
            new_history = conversation.history.copy()
            new_history.append(history_entry)
            await conv_mgr.update_conversation(
                unified_msg_origin=umo,
                conversation_id=curr_cid,
                history=new_history
            )

    async def call_model_segment(self, text: str) -> list[str]:
        # 懒加载ClientSession（避免Loop未初始化问题）
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

        # 优化提示词：明确禁止重复，强化分段逻辑
        prompt = f"""
任务：将以下文本拆分为3-5个自然段落，严格遵循以下规则：
1. 完整保留原文所有内容，不增删、不修改、不重复任何信息；
2. 按原文语义和标点（逗号、句号）断点拆分，每段1-2句话，保持逻辑连贯；
3. 仅返回分段纯文本，段落间用换行分隔，禁止加序号、标记、额外说明；
4. 去除每段末尾多余的标点符号（如逗号、顿号）。

### 原文开始 ###
{text}
### 原文结束 ###
        """
        payload = {
            "model": self.selected_model,
            "messages": [
                {"role": "system", "content": "仅执行文本分段任务，严格遵守所有规则，输出结果必须纯净"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0,  # 降低随机性，避免重复
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
            
            # 增强清洗逻辑：去重、去多余标点
            cleaned_segments = []
            seen = set()  # 避免分段重复
            for seg in segment_text.split("\n"):
                seg = seg.strip()
                if not seg or seg in seen:
                    continue
                # 移除开头序号和末尾多余标点
                cleaned_seg = re.sub(r'^[\d\.\s、，()【】]*', '', seg).strip()
                cleaned_seg = re.sub(r'[，,。；;、]+$', '', cleaned_seg).strip()
                if cleaned_seg:
                    seen.add(cleaned_seg)
                    cleaned_segments.append(cleaned_seg)
            return cleaned_segments

    async def terminate(self):
        # 安全关闭ClientSession
        if self.session and not self.session.closed:
            await self.session.close()
        logger.info(f"智能分段回复插件已卸载（当前模型：{self.selected_model}），资源已释放")
