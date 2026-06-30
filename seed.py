"""Run once to set up Turso tables + seed data, and embed docs into Qdrant."""
import os
from dotenv import load_dotenv

load_dotenv()

from db import execute
from langchain_community.embeddings import FastEmbedEmbeddings
from langchain_qdrant import QdrantVectorStore
from langchain_core.documents import Document


# ── Turso ──────────────────────────────────────────────────────────────────

DDL = [
    """CREATE TABLE IF NOT EXISTS employees (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        department TEXT,
        role TEXT,
        manager TEXT,
        leave_balance INTEGER,
        email TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS tickets (
        id TEXT PRIMARY KEY,
        title TEXT,
        description TEXT,
        priority TEXT,
        status TEXT,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS reports (
        id TEXT PRIMARY KEY,
        type TEXT,
        period TEXT,
        summary TEXT
    )""",
]

EMPLOYEES = [
    ("EMP001", "Priya Sharma",   "Engineering",  "Senior SWE",        "Rajesh Kumar",  18, "priya@nexus.io"),
    ("EMP002", "Arjun Mehta",    "Sales",         "Account Executive", "Sneha Patel",   12, "arjun@nexus.io"),
    ("EMP003", "Sneha Patel",    "Sales",         "Sales Manager",     "Vikram Singh",   8, "sneha@nexus.io"),
    ("EMP004", "Rahul Verma",    "HR",            "HR Business Partner","Anita Roy",     15, "rahul@nexus.io"),
    ("EMP005", "Anita Roy",      "HR",            "HR Head",           "CEO",            6, "anita@nexus.io"),
    ("EMP006", "Vikram Singh",   "Operations",   "COO",               "CEO",            5, "vikram@nexus.io"),
    ("EMP007", "Kavya Nair",     "Engineering",  "DevOps Engineer",   "Rajesh Kumar",  20, "kavya@nexus.io"),
    ("EMP008", "Rajesh Kumar",   "Engineering",  "VP Engineering",    "CEO",            4, "rajesh@nexus.io"),
]

REPORTS = [
    ("RPT001", "sales", "Q1 2025", "Q1 revenue: ₹4.2Cr (+18% YoY). Top performer: Arjun Mehta (₹92L). New customers acquired: 34. Pipeline coverage: 3.1x. Churn rate: 2.4%. Target achievement: 107%."),
    ("RPT002", "hr",   "Q1 2025", "Headcount: 142 (+8 QoQ). Attrition: 4.2% (industry avg 6%). Avg CSAT: 8.4/10. Open positions: 7. Training hours per employee: 12. Leave utilization: 68%."),
    ("RPT003", "it",   "Q1 2025", "Tickets raised: 218. Avg resolution time: 6.4h. SLA breaches: 3 (1.4%). Top issues: VPN (32%), laptop setup (18%), email access (14%). Infra uptime: 99.7%."),
]

print("Creating Turso tables...")
for ddl in DDL:
    execute(ddl)

print("Seeding employees...")
for emp in EMPLOYEES:
    execute(
        "INSERT OR REPLACE INTO employees (id, name, department, role, manager, leave_balance, email) VALUES (?, ?, ?, ?, ?, ?, ?)",
        list(emp),
    )

print("Seeding reports...")
for rep in REPORTS:
    execute(
        "INSERT OR REPLACE INTO reports (id, type, period, summary) VALUES (?, ?, ?, ?)",
        list(rep),
    )

print("Turso seeding done.")


# ── Qdrant ─────────────────────────────────────────────────────────────────

FAQ_DOCS = [
    Document(page_content="Employees are entitled to 24 days of casual leave per year. Unused leave lapses at year end and cannot be encashed. Leave must be applied at least 2 days in advance except for emergencies.", metadata={"source": "hr_leave_policy"}),
    Document(page_content="Medical/sick leave: 12 days per year. Requires a medical certificate for absences over 3 consecutive days. Sick leave does not lapse and accumulates up to 90 days.", metadata={"source": "hr_leave_policy"}),
    Document(page_content="Work from home is allowed up to 2 days per week for roles approved by department heads. WFH requires prior approval in the HR portal and cannot exceed 8 days in a calendar month.", metadata={"source": "hr_wfh_policy"}),
    Document(page_content="Travel reimbursement: Economy class for domestic flights, cab reimbursement capped at ₹1,500/day. Submit claims within 15 days of travel with receipts via the expense portal.", metadata={"source": "hr_reimbursement"}),
    Document(page_content="VPN access is provided via Cisco AnyConnect. If VPN is not connecting, try: (1) check internet connectivity, (2) restart AnyConnect, (3) clear DNS cache, (4) reinstall client. Raise an IT ticket if issue persists.", metadata={"source": "it_vpn_guide"}),
    Document(page_content="For laptop setup and hardware requests, raise a P2 ticket in the IT portal. Standard SLA is 2 business days. New employee laptop requests must be raised 5 days before joining.", metadata={"source": "it_hardware_guide"}),
    Document(page_content="Email access issues: contact IT helpdesk on ext. 400 or raise a P1 ticket. Account lockouts are resolved within 1 hour. Password resets can be self-served at reset.nexus.io.", metadata={"source": "it_email_guide"}),
    Document(page_content="Performance reviews are conducted bi-annually in April and October. Ratings: Exceeds Expectations, Meets Expectations, Needs Improvement. Promotions are tied to the October cycle.", metadata={"source": "hr_performance_policy"}),
    Document(page_content="Maternity leave: 26 weeks as per Maternity Benefit Act. Paternity leave: 10 days. Adoption leave: 12 weeks for the primary caregiver.", metadata={"source": "hr_parental_leave"}),
    Document(page_content="Employee referral bonus: ₹25,000 for successful hire in Engineering/Sales, ₹15,000 for other departments. Paid after the referred employee completes 6 months.", metadata={"source": "hr_referral_policy"}),
]

print("Embedding docs into Qdrant...")
embeddings = FastEmbedEmbeddings(model_name="BAAI/bge-small-en-v1.5")
QdrantVectorStore.from_documents(
    FAQ_DOCS,
    embeddings,
    url=os.getenv("QDRANT_URL"),
    api_key=os.getenv("QDRANT_API_KEY"),
    collection_name="nexus_docs",
    force_recreate=True,
)
print("Qdrant seeding done.")
print("\nAll done. Run: uvicorn main:app --reload")
