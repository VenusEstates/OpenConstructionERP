# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""ERP Chat module manifest."""

from app.core.module_loader import InferenceDeclaration, InferenceRole, ModuleManifest

manifest = ModuleManifest(
    name="oe_erp_chat",
    version="0.1.0",
    display_name="ERP Chat",
    description="AI-powered chat with tool-calling for construction ERP data",
    author="OpenConstructionERP Core Team",
    category="core",
    depends=["oe_ai", "oe_projects"],
    auto_install=True,
    enabled=True,
    inference=InferenceDeclaration(
        role=InferenceRole.CALLS_MODEL,
        what=(
            "An answer to a question the user typed about their own project data, and which of "
            "twenty tools to call to get that data"
        ),
        basis=(
            "service.py posts to the Anthropic and OpenAI endpoints with its own HTTP client rather "
            "than only through the shared provider layer, which is why it is named twice by the gate. "
            "Nineteen of the twenty tools read. One writes: create_boq_item adds a position to a BOQ "
            "from arguments the model chose. The backend gates that on the caller having access to "
            "the project, which is the platform-wide owner or admin or team-member rule and not a "
            "confirmation step, so the model's output reaches the database on the same turn and any "
            "member of the project can cause it. Recorded here because a register that lists this "
            "beside a module that only renders text would be describing two different things with "
            "one word"
        ),
    ),
)
