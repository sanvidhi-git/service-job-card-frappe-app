// Copyright (c) 2026, Sanvidhi Tech Pvt Ltd and contributors

frappe.ui.form.on("Counter Cash Memo Item", {
	async item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item_code) return;
		const d = await frappe.xcall("car_cupid_service.api.pos.get_line", { item_code: row.item_code, customer: frm.doc.customer });
		frappe.model.set_value(cdt, cdn, {
			item_name: d.item_name,
			item_type: d.item_type,
			is_stock_item: d.is_stock_item,
			company: d.company,
			uom: d.uom,
			warehouse: d.warehouse,
			price_list_rate: d.price_list_rate,
		});
	},
	qty: (frm) => car_cupid.render_company_summary(frm),
	discount_percentage: (frm) => car_cupid.render_company_summary(frm),
});

frappe.ui.form.on("Counter Cash Memo", {
	setup(frm) {
		car_cupid.setup_bill_discount_query(frm);
		frm.set_query("vehicle", () => ({ filters: frm.doc.customer ? { customer: frm.doc.customer } : {} }));
		frm.set_query("item_code", "items", () => ({ filters: { is_sales_item: 1, disabled: 0, has_variants: 0 } }));
	},
	refresh(frm) {
		car_cupid.render_company_summary(frm);
		if (frm.is_new()) {
			frm.set_intro(__("Tip: the Car Cupid POS is faster for counter sales and takes the payment too."), "blue");
			frm.add_custom_button(__("Open POS"), () => car_cupid.open_pos({ tab: "sale" }));
			return;
		}
		if (frm.doc.invoice_batch) {
			frm.add_custom_button(__("Print Customer Memo"), () =>
				window.open(frappe.urllib.get_full_url(`/printview?doctype=Invoice%20Batch&name=${encodeURIComponent(frm.doc.invoice_batch)}&format=${encodeURIComponent("Car Cupid Consolidated Memo")}&trigger_print=1`))
			);
			frm.add_custom_button(__("Invoice Batch"), () => frappe.set_route("Form", "Invoice Batch", frm.doc.invoice_batch));
		}
		if (frm.doc.status === "Draft") {
			frm.add_custom_button(__("Cancel Memo"), () =>
				frappe.prompt({ fieldname: "reason", fieldtype: "Data", label: __("Reason") }, (v) => frm.call("cancel_memo", { reason: v.reason }).then(() => frm.reload_doc()))
			);
		}
	},
});

frappe.ui.form.on("Car Cupid Bill Discount", {
	discount_amount: (frm) => car_cupid.render_company_summary(frm),
	company: (frm) => car_cupid.render_company_summary(frm),
	bill_discounts_remove: (frm) => car_cupid.render_company_summary(frm),
});
