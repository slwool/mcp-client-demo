import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from typing import Optional
from dotenv import load_dotenv
from openai import OpenAI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# 加载 .env 文件，确保 API Key 受到保护
load_dotenv()

class MCPClient :
    def __init__(self):
        """初始化MCP客户端"""
        self.exit_stack = AsyncExitStack()
        # 从环境变量读取配置
        self.openai_api_key = os.getenv("OPENAI_API_KEY")  # 读取OPENAI_API_KEY
        print(f"self.openai_api_key: {self.openai_api_key}")
        self.base_url = os.getenv("OPENAI_API_BASE")  # 读取BASE_URL
        print(f"self.base_url: {self.base_url}")
        # self.model = os.getenv("OPENAI_MODEL") # 读取MODEL
        self.model = 'deepseek-chat' # 读取MODEL
        print(f"self.model: {self.model}")

        if not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY环境变量未设置")
        
        # 初始化OpenAI 客户端
        self.client = OpenAI(api_key=self.openai_api_key, base_url=self.base_url)
        self.session: Optional[ClientSession] = None

    async def connect_to_server(self, server_script_path: str):
        """连接到MCP服务器 并列出可用工具"""
        # 检查脚本类型
        is_python = server_script_path.endswith(".py")
        is_js = server_script_path.endswith(".js")
        if not (is_python or is_js):
            raise ValueError("脚本类型不支持，仅支持Python和JavaScript")
        
        # 根据脚本类型选择执行命令
        command = "python" if is_python else "node"
        server_params = StdioServerParameters(
            command=command,
            args=[server_script_path],
            env=None
        )

        # 启动 MCP 服务器并建立通信
        stdio_transport = await self.exit_stack.enter_async_context(
            stdio_client(server_params)
        )

        self.stdio, self.write = stdio_transport
        self.session = await self.exit_stack.enter_async_context(
            ClientSession(self.stdio, self.write)
        )
        await self.session.initialize()

        # 列出可用工具
        response = await self.session.list_tools()
        tools = response.tools
        print("\n已连接到服务器，支持以下工具:", [tool.name for tool in tools])

    async def process_query(self, query: str) -> str:
        """
        使用大模型处理查询并调用可用的 MCP 工具 (Function Calling)
        """
        messages = [{"role": "user", "content": query}]

        # 获取可用工具列表
        response = await self.session.list_tools()
        available_tools = [{
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.inputSchema
            }
        } for tool in response.tools]
    
        # 调用 OpenAI API
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=available_tools
        )    
        
        # 处理返回的内容
        content = response.choices[0]
        print(f"response={content}")
        if content.finish_reason == "tool_calls":
            # 处理工具调用
            tool_call = content.message.tool_calls[0]
            tool_name = tool_call.function.name
            tool_args = json.loads(tool_call.function.arguments)

            # 执行工具
            result = await self.session.call_tool(tool_name, tool_args)
            print(f"\n\n[Calling tool {tool_name} with args {tool_args}]\n\n")

            # 将结果存入消息历史
            messages.append(content.message.model_dump())
            messages.append({
                "role": "tool",
                "content": result.content[0].text,
                "tool_call_id": tool_call.id,
            })

            # 将结果返回给大模型生成最终响应
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
            )
            return response.choices[0].message.content

        return content.message.content

    async def chat_loop(self):
        """运行交互式聊天循环"""
        print("\n🤖 MCP 客户端已启动！输入 'quit' 退出")
        while True:
            try:
                query = input("\n你: ").strip()
                if query.lower() == 'quit':
                    break
                response = await self.process_query(query)  # 发送用户输入到 OpenAI API
                print(f"\n🤖 OpenAI: {response}")
            except Exception as e:
                print(f"\n⚠️ 发生错误: {str(e)}")
    
    async def cleanup(self):
        """清理资源"""
        await self.exit_stack.aclose()

async def main():
    """主函数"""
    if len(sys.argv) < 2:
        print("Usage: python client.py <path_to_server_script>")
        sys.exit(1)

    client = MCPClient()
    try:
        await client.connect_to_server(sys.argv[1])
        await client.chat_loop()
    finally:
        await client.cleanup()


if __name__ == "__main__":
    asyncio.run(main())