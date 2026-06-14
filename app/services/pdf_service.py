from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from app.config import TEMPLATES_DIR

template_environment = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)


def generate_invoice_pdf(template_context: dict, destination: Path) -> None:
    template = template_environment.get_template("invoice.html")
    rendered_html = template.render(**template_context)
    destination.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=rendered_html, base_url=str(TEMPLATES_DIR)).write_pdf(destination)
