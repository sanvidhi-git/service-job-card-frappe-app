"""Company access for Car Cupid staff.

ERPNext adds a User Permission (Company = the employee's company) for every Employee linked to a user.
Counter staff bill all three Car Cupid companies, so a user restricted to one company could not read
the other companies' accounts while billing. This keeps such users' Company permissions in step with
the enabled Car Cupid companies. Users with no Company restriction are left untouched (they already
see every company).
"""

import frappe
from frappe import _

from car_cupid_service.api.permissions import CC_ROLES
from car_cupid_service.services.settings import get_company_map

STAFF_ROLES = CC_ROLES - {"System Manager", "Incentive Manager"}


def staff_users() -> list[str]:
	return sorted(
		set(
			frappe.get_all(
				"Has Role",
				filters={"parenttype": "User", "role": ("in", list(STAFF_ROLES))},
				pluck="parent",
			)
		)
		- {"Administrator", "Guest"}
	)


def missing_companies(user: str) -> list[str]:
	allowed = set(
		frappe.get_all("User Permission", filters={"user": user, "allow": "Company"}, pluck="for_value")
	)
	if not allowed:
		return []  # not restricted by company
	return [c for c in get_company_map() if c not in allowed]


def sync_company_access(users: list[str] | None = None) -> dict:
	added = {}
	previous = frappe.flags.get("cc_syncing_access")
	frappe.flags.cc_syncing_access = True  # our own inserts must not re-trigger the hook
	try:
		for user in users or staff_users():
			for company in missing_companies(user):
				if frappe.db.exists(
					"User Permission", {"user": user, "allow": "Company", "for_value": company}
				):
					continue
				frappe.get_doc(
					{
						"doctype": "User Permission",
						"user": user,
						"allow": "Company",
						"for_value": company,
						"apply_to_all_doctypes": 1,
						"is_default": 0,
					}
				).insert(ignore_permissions=True)
				added.setdefault(user, []).append(company)
	finally:
		frappe.flags.cc_syncing_access = previous
	return added


@frappe.whitelist()
def grant_company_access() -> dict:
	frappe.only_for("System Manager")
	added = sync_company_access()
	frappe.msgprint(
		_("Company access added for {0} user(s).").format(len(added))
		if added
		else _("All Car Cupid staff already have access to every enabled company."),
		alert=True,
	)
	return added


def check_billing_access(companies: list[str]):
	"""Clear error before billing if the current user is still restricted away from a company."""
	missing = [c for c in companies if c in missing_companies(frappe.session.user)]
	if missing:
		frappe.throw(
			_(
				"Your user is limited to other companies and can't bill {0}. Ask your System Manager to click 'Grant Company Access to Staff' in Car Cupid Settings."
			).format(", ".join(missing)),
			frappe.PermissionError,
			title=_("Company Access Needed"),
		)


def on_user_update(doc, method=None):
	"""Keep access in step when someone is given a Car Cupid role."""
	if {r.role for r in doc.get("roles") or []} & STAFF_ROLES:
		sync_company_access([doc.name])


def on_user_permission_insert(doc, method=None):
	"""ERPNext adds a Company permission when an Employee gets a user id: widen it at once for staff."""
	if doc.allow != "Company" or frappe.flags.get("cc_syncing_access"):
		return
	if set(frappe.get_roles(doc.user)) & STAFF_ROLES:
		sync_company_access([doc.user])
