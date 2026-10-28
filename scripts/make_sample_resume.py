"""Generate data/sample_resumes/sample_sde_resume.pdf (a synthetic resume, no real person)."""
import sys
from pathlib import Path

import pymupdf

SAMPLE_RESUME_LINES = """Alex Morgan
alex.morgan@example.com | +1 555 0100 | github.com/alexmorgan-demo

SUMMARY
Software engineer with 2 years of experience building backend services and applied ML features.

EXPERIENCE
Backend Engineer | Northwind Labs | 2023 - Present
• Built a Python FastAPI service handling 2M requests per day with Redis caching.
• Reduced p95 latency from 800 ms to 210 ms by adding async database access and query indexes.
• Led migration of a monolith billing module to Docker containers deployed on AWS ECS.

Software Engineering Intern | Contoso Systems | 2022
• Wrote React dashboards consuming REST APIs and added unit tests with pytest and Jest.
• Automated nightly data validation jobs in Python, cutting manual checks by 6 hours a week.

PROJECTS
Ocular AI | Python, PyTorch, FastAPI, Docker
• Built a retinal image classifier reaching 94% accuracy on a public diabetic retinopathy dataset.
• Served the model through a FastAPI endpoint with batch inference and a React upload UI.

DocuChat RAG | LangChain, Chroma, Gemini
• Created a retrieval-augmented chatbot over PDF documents using hybrid search and reranking.
• Evaluated chunking strategies with hit rate and MRR on 40 labelled questions.

EDUCATION
B.Tech in Computer Science | Example Institute of Technology | 2018 - 2022

SKILLS
Languages: Python, JavaScript, SQL, Java
Frameworks: FastAPI, React, LangChain, PyTorch
Tools: Docker, AWS (S3, ECS, EC2), MongoDB, PostgreSQL, Git
""".splitlines()


def build_pdf(path: Path, lines: list[str] = SAMPLE_RESUME_LINES) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    y = 50
    for line in lines:
        if not line.strip():
            y += 8  # visible gap so PyMuPDF splits layout blocks
            continue
        page.insert_text((50, y), line, fontsize=10, fontname="helv")
        y += 14
    doc.save(path)


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "data/sample_resumes/sample_sde_resume.pdf")
    out.parent.mkdir(parents=True, exist_ok=True)
    build_pdf(out)
    print("wrote", out)
