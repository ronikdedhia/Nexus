"""Run once to set up Turso tables + seed data, and embed docs into Qdrant."""
import os
import uuid
from dotenv import load_dotenv

load_dotenv()

from db import execute, migrate
from embeddings import FastEmbedEmbeddings
from langchain_qdrant import QdrantVectorStore
from langchain_core.documents import Document


# ── Turso ──────────────────────────────────────────────────────────────────


EMPLOYEES = [
    ("EMP001", "Priya Sharma",   "Engineering",  "Senior SWE",        "Rajesh Kumar",  18, "priya@nexus.io"),
    ("EMP002", "Arjun Mehta",    "Sales",         "Account Executive", "Sneha Patel",   12, "arjun@nexus.io"),
    ("EMP003", "Sneha Patel",    "Sales",         "Sales Manager",     "Vikram Singh",   8, "sneha@nexus.io"),
    ("EMP004", "Rahul Verma",    "HR",            "HR Business Partner","Anita Roy",     15, "rahul@nexus.io"),
    ("EMP005", "Anita Roy",      "HR",            "HR Head",           "CEO",            6, "anita@nexus.io"),
    ("EMP006", "Vikram Singh",   "Operations",   "COO",               "CEO",            5, "vikram@nexus.io"),
    ("EMP007", "Kavya Nair",     "Engineering",  "DevOps Engineer",   "Rajesh Kumar",  20, "kavya@nexus.io"),
    ("EMP008", "Rajesh Kumar",   "Engineering",  "VP Engineering",    "CEO",            4, "rajesh@nexus.io"),
    ("EMP009", "Meera Iyer",     "Engineering",  "SWE II",            "Priya Sharma",  22, "meera@nexus.io"),
    ("EMP010", "Aditya Rao",     "Engineering",  "SWE I",             "Priya Sharma",  24, "aditya@nexus.io"),
    ("EMP011", "Neha Kapoor",    "Engineering",  "QA Engineer",       "Kavya Nair",    16, "neha@nexus.io"),
    ("EMP012", "Karan Malhotra", "Sales",         "Account Executive", "Sneha Patel",    9, "karan@nexus.io"),
    ("EMP013", "Divya Menon",    "Sales",         "SDR",               "Sneha Patel",   14, "divya@nexus.io"),
    ("EMP014", "Suresh Pillai",  "Marketing",     "Marketing Manager", "Vikram Singh",  10, "suresh@nexus.io"),
    ("EMP015", "Pooja Reddy",    "Marketing",     "Content Strategist","Suresh Pillai", 19, "pooja@nexus.io"),
    ("EMP016", "Farhan Khan",    "HR",            "Talent Acquisition","Anita Roy",     11, "farhan@nexus.io"),
    ("EMP017", "Ritu Choudhary", "Finance",       "Finance Manager",   "CEO",            7, "ritu@nexus.io"),
    ("EMP018", "Manish Gupta",   "Finance",       "Accountant",        "Ritu Choudhary",17, "manish@nexus.io"),
    ("EMP019", "Ishaan Bhatt",   "Operations",   "Facilities Lead",   "Vikram Singh",  13, "ishaan@nexus.io"),
    ("EMP020", "Tanvi Desai",    "Engineering",  "Engineering Manager","Rajesh Kumar",   9, "tanvi@nexus.io"),
]

REPORTS = [
    ("RPT001", "sales", "Q1 2025", "Q1 revenue: ₹4.2Cr (+18% YoY). Top performer: Arjun Mehta (₹92L). New customers acquired: 34. Pipeline coverage: 3.1x. Churn rate: 2.4%. Target achievement: 107%."),
    ("RPT002", "hr",   "Q1 2025", "Headcount: 142 (+8 QoQ). Attrition: 4.2% (industry avg 6%). Avg CSAT: 8.4/10. Open positions: 7. Training hours per employee: 12. Leave utilization: 68%."),
    ("RPT003", "it",   "Q1 2025", "Tickets raised: 218. Avg resolution time: 6.4h. SLA breaches: 3 (1.4%). Top issues: VPN (32%), laptop setup (18%), email access (14%). Infra uptime: 99.7%."),
    ("RPT004", "sales", "Q2 2025", "Q2 revenue: ₹4.8Cr (+14% QoQ). Top performer: Karan Malhotra (₹78L). New customers acquired: 41. Pipeline coverage: 2.9x. Churn rate: 2.1%. Target achievement: 112%."),
    ("RPT005", "hr",   "Q2 2025", "Headcount: 151 (+9 QoQ). Attrition: 3.8% (industry avg 6%). Avg CSAT: 8.6/10. Open positions: 9. Training hours per employee: 15. Leave utilization: 71%."),
    ("RPT006", "it",   "Q2 2025", "Tickets raised: 246. Avg resolution time: 5.8h. SLA breaches: 2 (0.8%). Top issues: laptop setup (26%), VPN (24%), software access (16%). Infra uptime: 99.9%."),
]

print("Creating Turso tables and applying migrations...")
migrate()

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
    Document(page_content="Expense claim approval: claims under ₹5,000 are auto-approved by the Finance Manager, claims above ₹5,000 require CEO sign-off. Submit via the finance portal with itemized receipts. Reimbursement is processed within 7 business days.", metadata={"source": "finance_expense_policy"}),
    Document(page_content="Invoice and vendor payments: standard payment terms are net 30 days from invoice receipt. Vendor onboarding requires PAN, GST details, and a signed W-9/vendor agreement uploaded to the finance portal before the first payment.", metadata={"source": "finance_vendor_policy"}),
    Document(page_content="Marketing brand guidelines: use the approved logo pack from the brand portal, primary color #1A2B4C, secondary #F5A623. All external-facing decks and social posts must go through the Marketing Manager for brand review before publishing.", metadata={"source": "marketing_brand_guide"}),
    Document(page_content="Campaign budget approval: campaigns under ₹1L are approved by the Marketing Manager directly. Campaigns above ₹1L require sign-off from the COO. Submit budget requests via the marketing planning sheet at least 2 weeks before launch.", metadata={"source": "marketing_campaign_policy"}),
    Document(page_content="Software/tool access requests: to request access to a new SaaS tool (Figma, Salesforce, etc.), raise a P3 ticket with business justification. Requests are reviewed weekly by IT and Finance for licensing cost approval.", metadata={"source": "it_software_access_guide"}),
]

print("Embedding docs into Qdrant...")
embeddings = FastEmbedEmbeddings()
# Stable IDs per seed doc: re-running overwrites these points in place instead of duplicating them,
# and the collection is no longer recreated, so documents added via ingest_docs.py or the API survive.
seed_ids = [
    str(uuid.uuid5(uuid.NAMESPACE_URL, f"nexus-seed:{doc.metadata['source']}:{i}"))
    for i, doc in enumerate(FAQ_DOCS)
]
QdrantVectorStore.from_documents(
    FAQ_DOCS,
    embeddings,
    ids=seed_ids,
    url=os.getenv("QDRANT_URL"),
    api_key=os.getenv("QDRANT_API_KEY"),
    collection_name="nexus_docs",
)
print("Qdrant seeding done.")
print("\nAll done. Run: uvicorn main:app --reload")
