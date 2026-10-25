"""Thin aliases matching the blueprint's API names (§16)."""

import frappe

from car_cupid_service.services import billing_service


@frappe.whitelist()
def validate_source(source_doctype: str, source_name: str) -> dict:
	return billing_service.preview(source_doctype, source_name)


@frappe.whitelist()
def create_invoice_batch(source_doctype: str, source_name: str, request_key: str, payments=None, allow_credit: int = 0):
	return billing_service.bill_source(
		source_doctype, source_name, request_key, frappe.parse_json(payments) if payments else [], bool(int(allow_credit or 0))
	)


@frappe.whitelist()
def retry_failed_company(batch: str):
	return billing_service.retry_batch(batch)
