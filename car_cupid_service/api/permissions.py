import frappe

CC_ROLES = {
	"Counter Salesperson",
	"Service Advisor",
	"Service Engineer",
	"Storekeeper",
	"Cashier",
	"Billing Supervisor",
	"Accounts Manager",
	"Incentive Manager",
	"System Manager",
}
SESSION_OVERSEERS = {"Billing Supervisor", "Accounts Manager", "System Manager"}


def has_app_permission() -> bool:
	if frappe.session.user == "Administrator":
		return True
	return bool(CC_ROLES.intersection(frappe.get_roles()))


def counter_session_query(user: str | None = None) -> str:
	user = user or frappe.session.user
	if user == "Administrator" or SESSION_OVERSEERS.intersection(frappe.get_roles(user)):
		return ""
	return f"`tabCounter Session`.cashier = {frappe.db.escape(user)}"


def counter_session_has_permission(doc, ptype=None, user=None):
	user = user or frappe.session.user
	if user == "Administrator" or SESSION_OVERSEERS.intersection(frappe.get_roles(user)):
		return True
	return doc.cashier == user
