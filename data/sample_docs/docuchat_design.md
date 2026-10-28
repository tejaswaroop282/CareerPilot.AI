# DocuChat RAG design document

DocuChat lets users ask questions about their own PDF collections and get answers with citations.

## Ingestion

Files are parsed with PyMuPDF. Text is split by headings first, then into chunks of about 500 tokens with a 12 percent overlap. Tables and code blocks are never split across chunks. Each chunk keeps a contextual header made of the file name and heading path.

## Embeddings and storage

Chunks are embedded with the Gemini embedding model and stored in a persistent Chroma collection with metadata for source file, page and heading. MongoDB stores chat history and uploaded file records.

## Retrieval

Retrieval is hybrid. A dense vector search and a BM25 keyword search each return their top 20 chunks, which are merged with reciprocal rank fusion using a constant of 60. The top 30 merged candidates are reranked by the ms-marco MiniLM cross-encoder and the best 5 go to the language model.

## Evaluation

A set of 40 labelled questions measures retrieval. Vector-only search reached hit rate at 5 of 0.71 and MRR of 0.58. Hybrid search with reranking raised these to 0.86 and 0.74. Chunking experiments showed heading-aware chunks beating fixed 400-token windows by 8 points of hit rate.

## Guardrails

Every answer must cite chunk ids, and a validator rejects answers that cite ids that were not retrieved. When no chunk scores above the relevance threshold the assistant says it does not know instead of guessing.

## Challenges

Tables inside PDFs were first split in the middle of rows, which destroyed numeric answers. Keeping table blocks whole fixed this. Scanned PDFs needed OCR, which was left out of the first version.
