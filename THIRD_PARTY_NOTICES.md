# Third-Party License Notices

NetworkOps AI Agent 的原创代码依据根目录 [MIT License](LICENSE) 发布。MIT License 仅适用于本项目原创代码，不会替代、修改或重新许可任何第三方组件、模型或其许可证义务。

本清单依据 2026-07-15 本地安装版本的包元数据和上游官方许可证资料整理。升级依赖或模型后应重新审计。

## 直接依赖

| 组件 | 审计版本 | 许可证 | MIT 项目兼容性 | 官方来源 |
| --- | ---: | --- | --- | --- |
| FastAPI | 0.139.0 | MIT | 兼容；保留版权与许可声明 | [fastapi/fastapi](https://github.com/fastapi/fastapi/blob/master/LICENSE) |
| Uvicorn | 0.51.0 | BSD-3-Clause | 兼容；保留版权、条件与免责声明 | [Kludex/uvicorn](https://github.com/Kludex/uvicorn/blob/main/LICENSE.md) |
| LangGraph | 1.2.9 | MIT | 兼容；保留版权与许可声明 | [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph/blob/main/LICENSE) |
| langgraph-checkpoint-sqlite | 3.1.0 | MIT | 兼容；保留版权与许可声明 | [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-sqlite) |
| pydantic-settings | 2.14.2 | MIT | 兼容；保留版权与许可声明 | [pydantic/pydantic-settings](https://github.com/pydantic/pydantic-settings) |
| Pydantic | 2.13.4 | MIT | 兼容；保留版权与许可声明 | [pydantic/pydantic](https://github.com/pydantic/pydantic/blob/main/LICENSE) |
| langchain-core | 1.4.9 | MIT | 兼容；保留版权与许可声明 | [langchain-ai/langchain](https://github.com/langchain-ai/langchain/blob/master/LICENSE) |
| langchain-text-splitters | 1.1.2 | MIT | 兼容；保留版权与许可声明 | [langchain-ai/langchain](https://github.com/langchain-ai/langchain/blob/master/LICENSE) |
| langchain-chroma | 1.1.0 | MIT | 兼容；这是 LangChain 适配包，不等同于 Chroma 引擎许可证 | [langchain-ai/langchain](https://github.com/langchain-ai/langchain/blob/master/LICENSE) |
| langchain-huggingface | 1.2.2 | MIT | 兼容；保留版权与许可声明 | [langchain-ai/langchain](https://github.com/langchain-ai/langchain/blob/master/LICENSE) |
| sentence-transformers | 5.6.0 | Apache-2.0 | 兼容；再分发时遵守许可证与适用的 NOTICE 要求 | [huggingface/sentence-transformers](https://github.com/huggingface/sentence-transformers) |
| PyMuPDF4LLM | 1.28.0 | GNU AGPL v3 或 Artifex 商业许可 | **条件兼容/需单独合规**；见下方专项说明 | [PyMuPDF 官方 FAQ](https://pymupdf.readthedocs.io/en/latest/faq/index.html) |
| NetworkX | 3.6.1 | BSD-3-Clause | 兼容；保留版权、条件与免责声明 | [networkx/networkx](https://github.com/networkx/networkx) |
| Streamlit | 1.59.1 | Apache-2.0 | 兼容；再分发时遵守许可证与适用的 NOTICE 要求 | [streamlit/streamlit](https://github.com/streamlit/streamlit) |

## 重要运行时组件与模型

| 组件 | 审计版本 | 许可证 | MIT 项目兼容性 | 官方来源 |
| --- | ---: | --- | --- | --- |
| Chroma / chromadb | 1.5.9 | Apache-2.0 | 兼容；与 MIT 许可的 `langchain-chroma` 适配包分开适用 | [chroma-core/chroma](https://github.com/chroma-core/chroma) |
| BAAI/bge-m3 | 当前模型仓库版本 | MIT | 兼容；模型文件仍适用其自身 MIT 条款 | [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3/tree/main) |

## PyMuPDF4LLM 专项说明

PyMuPDF 官方说明 PyMuPDF4LLM 与 PyMuPDF、MuPDF 采用相同的双许可模式：GNU AGPL 或 Artifex 商业许可；将其用于 RAG 系统的 PDF 解析数据管线也不会自动免除相关义务。

因此：

- 本项目的 MIT License 仅许可 NetworkOps AI Agent 的原创代码，不覆盖 PyMuPDF4LLM；
- 使用或分发 PDF 解析功能时，使用者必须自行遵守适用的 GNU AGPL v3 条款，或取得有效的 Artifex 商业许可；
- 不能仅凭本项目的 MIT License 推断整个安装环境或组合分发物均为 MIT；
- 若要在闭源、商业或网络服务场景中使用，应在发布或部署前完成独立的许可证评估。

## 免责声明

本文件是基于当前依赖版本的工程许可证审计记录，不构成法律意见。许可证文本和上游项目条款发生变化时，以对应组件的正式许可证文件为准。
