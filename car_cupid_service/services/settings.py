"""Cached access to Car Cupid Settings and the company routing map."""

import frappe
from frappe import _
from frappe.utils import cint, flt

SUPERVISOR_ROLES = {"Billing Supervisor", "Accounts Manager", "System Manager"}


def get_settings():
	return frappe.get_cached_doc("Car Cupid Settings")


def get_company_map() -> dict:
	"""Enabled companies keyed by company name -> row (frappe._dict)."""
	settings = get_settings()
	return {row.company: frappe._dict(row.as_dict()) for row in settings.companies if cint(row.enabled)}


def get_company_row(company: str, throw=True):
	row = get_company_map().get(company)
	if not row and throw:
		frappe.throw(
			_("Company {0} is not enabled in Car Cupid Settings.").format(frappe.bold(company)),
			title=_("Company Not Enabled"),
		)
	return row


def get_default_company() -> str | None:
	settings = get_settings()
	companies = get_company_map()
	if settings.default_company and settings.default_company in companies:
		return settings.default_company
	return next(iter(companies), None)


def is_supervisor(user: str | None = None) -> bool:
	return bool(SUPERVISOR_ROLES.intersection(frappe.get_roles(user)))


def issue_required() -> bool:
	return get_settings().material_issue_policy == "Issue Required for Stock Items"


def material_flow_enabled() -> bool:
	return get_settings().material_issue_policy != "Not Required"


def max_discount() -> float:
	return flt(get_settings().max_discount_without_approval)


def cash_mode() -> str:
	return get_settings().cash_mode_of_payment or "Cash"


def denominations() -> list[float]:
	raw = get_settings().denominations or "500,200,100,50,20,10,5,2,1"
	out = []
	for part in raw.split(","):
		part = part.strip()
		if part:
			try:
				out.append(float(part))
			except ValueError:
				continue
	return sorted(set(out), reverse=True)
