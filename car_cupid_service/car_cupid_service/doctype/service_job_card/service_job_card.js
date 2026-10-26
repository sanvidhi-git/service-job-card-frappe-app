// Copyright (c) 2026, Sanvidhi Tech Pvt Ltd and contributors

const cc_line_events = {
	async item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item_code) return;
		const d = await frappe.xcall("car_cupid_service.api.pos.get_line", { item_code: row.item_code, customer: frm.doc.customer });
		// company is routed from Item Defaults on the server; don't keep the user's default company
		frappe.model.set_value(cdt, cdn, {
			item_name: d.item_name,
			item_type: d.item_type,
			is_stock_item: d.is_stock_item,
			company: d.company,
			uom: d.uom,
			warehouse: d.warehouse,
			price_list_rate: d.price_list_rate,
			sales_incentive_rate: d.sales_incentive_rate,
			sales_person: row.sales_person || frm.doc.default_sales_person,
		});
		if (d.is_stock_item && d.actual_qty != null && d.actual_qty <= 0) {
			frappe.show_alert({ message: __("{0} is out of stock in {1}", [d.item_name, d.warehouse]), indicator: "orange" });
		}
	},
	async company(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item_code || !row.company) return;
		const d = await frappe.xcall("car_cupid_service.api.pos.get_line", { item_code: row.item_code, company: row.company, customer: frm.doc.customer });
		frappe.model.set_value(cdt, cdn, { warehouse: d.warehouse, price_list_rate: d.price_list_rate });
	},
	qty: (frm, cdt, cdn) => cc_calc(frm, cdt, cdn),
	price_list_rate: (frm, cdt, cdn) => cc_calc(frm, cdt, cdn),
	discount_percentage: (frm, cdt, cdn) => cc_calc(frm, cdt, cdn),
	items_remove: (frm) => car_cupid.render_company_summary(frm),
};

function cc_calc(frm, cdt, cdn) {
	const r = locals[cdt][cdn];
	const rate = flt(r.price_list_rate) * (1 - flt(r.discount_percentage) / 100);
	frappe.model.set_value(cdt, cdn, { rate, amount: rate * flt(r.qty) });
	car_cupid.render_company_summary(frm);
}

frappe.ui.form.on("Service Job Card Item", cc_line_events);

frappe.ui.form.on("Service Job Card", {
	setup(frm) {
		car_cupid.setup_bill_discount_query(frm);
		frm.set_query("vehicle", () => ({ filters: frm.doc.customer ? { customer: frm.doc.customer, status: "Active" } : { status: "Active" } }));
		frm.set_query("item_code", "items", () => ({ filters: { is_sales_item: 1, disabled: 0, has_variants: 0 } }));
		frm.set_query("warehouse", "items", (doc, cdt, cdn) => ({ filters: { company: locals[cdt][cdn].company, is_group: 0 } }));
	},
	async vehicle(frm) {
		if (!frm.doc.vehicle) return;
		const v = await frappe.db.get_value("Customer Vehicle", frm.doc.vehicle, ["customer", "last_odometer"]);
		const m = v.message || {};
		if (m.customer && !frm.doc.customer) frm.set_value("customer", m.customer);
		if (m.last_odometer && !frm.doc.odometer) frm.set_df_property("odometer", "description", __("Last recorded: {0} km", [m.last_odometer]));
	},
	refresh(frm) {
		car_cupid.render_company_summary(frm);
		frm.set_intro("");
		const status_color = { Open: "blue", "Work In Progress": "orange", "Ready for Billing": "green", "Partially Billed": "orange", Billed: "green", Delivered: "gray", Cancelled: "red" };
		frm.page.set_indicator(__(frm.doc.status), status_color[frm.doc.status] || "gray");
		if (frm.is_new()) return;

		frm.add_custom_button(__("Open in POS"), () => car_cupid.open_pos({ job_card: frm.doc.name }));
		if (["Cancelled", "Delivered"].includes(frm.doc.status)) return;

		const stock_pending = frm.doc.items.some((r) => r.is_stock_item && r.work_status !== "Cancelled" && flt(r.qty) > flt(r.requested_qty));
		if (stock_pending) {
			frm.add_custom_button(__("Request Materials"), () =>
				frappe.xcall("car_cupid_service.api.job_card.request_materials", { job_card: frm.doc.name }).then(() => frm.reload_doc())
			, __("Actions"));
		}
		if (frm.doc.items.some((r) => !r.is_stock_item && !["Completed", "Cancelled"].includes(r.work_status))) {
			frm.add_custom_button(__("Mark All Work Completed"), () =>
				frm.call("set_work_status", { status: "Completed" }).then(() => frm.reload_doc())
			, __("Actions"));
		}
		if (["Ready for Billing", "Partially Billed"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Bill & Collect Payment"), () => car_cupid.open_pos({ job_card: frm.doc.name })).addClass("btn-primary");
			frm.set_intro(__("All work and parts are ready. Bill it from the POS so each company gets its own invoice."), "green");
		}
		if (frm.doc.status === "Billed") {
			frm.add_custom_button(__("Hand Over Vehicle"), () => frm.call("mark_delivered").then(() => frm.reload_doc())).addClass("btn-primary");
		}
		if (!flt(frm.doc.total_billed_amount)) {
			frm.add_custom_button(__("Cancel Job"), () =>
				frappe.prompt({ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 }, (v) =>
					frm.call("cancel_job", { reason: v.reason }).then(() => frm.reload_doc())
				)
			, __("Actions"));
		}
		if (frm.doc.promised_delivery && frm.doc.promised_delivery < frappe.datetime.now_datetime() && frm.doc.billing_status !== "Fully Billed") {
			frm.dashboard.set_headline_alert(__("Promised delivery time has passed."), "red");
		}
	},
});

frappe.ui.form.on("Car Cupid Bill Discount", {
	discount_amount: (frm) => car_cupid.render_company_summary(frm),
	company: (frm) => car_cupid.render_company_summary(frm),
	bill_discounts_remove: (frm) => car_cupid.render_company_summary(frm),
});
