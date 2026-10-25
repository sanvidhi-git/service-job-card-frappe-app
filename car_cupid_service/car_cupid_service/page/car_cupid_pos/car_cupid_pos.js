// Car Cupid POS — counter sale, job cards, stores, cash drawer and billing recovery.
// All prices, taxes, company routing and permissions are decided on the server; this page only
// collects input, shows what the server returns and keeps an unsent sale safe in localStorage.

frappe.pages["car-cupid-pos"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Car Cupid POS"), single_column: true });
	wrapper.ccpos = new CarCupidPOS(page, wrapper);
};

frappe.pages["car-cupid-pos"].on_page_show = function (wrapper) {
	const pos = wrapper.ccpos;
	if (!pos) return;
	const opts = frappe.route_options || {};
	frappe.route_options = null;
	if (opts.job_card) pos.open_job(opts.job_card);
	else if (opts.tab) pos.go(opts.tab);
	else pos.refresh_counts();
};

const CCP_SEARCH_ICON =
	'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>';

class CarCupidPOS {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.tab = "sale";
		this.counts = {};
		this.$root = $('<div class="ccp"></div>').appendTo(page.main);
		this.storage_key = `ccp-sale-${frappe.session.user}`;
		this.setup_actions();
		this.load_boot();
		this.bind_keys();
	}

	// ------------------------------------------------------------------ infrastructure
	call(method, args = {}, freeze = false) {
		return new Promise((resolve, reject) => {
			frappe.call({
				method,
				args,
				freeze,
				freeze_message: freeze ? __("Working…") : undefined,
				callback: (r) => resolve(r.message),
				error: (e) => reject(e || {}),
			});
		});
	}

	esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}

	money(v) {
		return format_currency(flt(v), frappe.defaults.get_default("currency") || "INR");
	}

	company(c) {
		return (this.boot.companies || []).find((x) => x.company === c) || { company: c, label: c, color: null };
	}

	chip(c, extra = "") {
		const co = this.company(c);
		const dot = `<i style="background:${co.color || "var(--gray-500)"}"></i>`;
		return `<span class="ccp-chip ${extra}" title="${this.esc(co.company)}">${dot}${this.esc(co.label)}</span>`;
	}

	setup_actions() {
		this.page.set_secondary_action(__("Full screen"), () => this.toggle_kiosk(), "expand");
		this.page.add_menu_item(__("Car Cupid Settings"), () => frappe.set_route("Form", "Car Cupid Settings"));
		this.page.add_menu_item(__("Job Card Status report"), () => frappe.set_route("query-report", "Job Card Status"));
		this.page.add_menu_item(__("Keyboard shortcuts"), () =>
			frappe.msgprint(
				`<table class="table table-sm"><tr><td><span class="ccp-kbd">F2</span></td><td>${__("Scan / search items")}</td></tr>
				<tr><td><span class="ccp-kbd">F4</span></td><td>${__("Find customer")}</td></tr>
				<tr><td><span class="ccp-kbd">F9</span></td><td>${__("Collect payment")}</td></tr>
				<tr><td><span class="ccp-kbd">Enter</span></td><td>${__("Add the scanned / first item")}</td></tr>
				<tr><td><span class="ccp-kbd">Esc</span></td><td>${__("Clear the search box")}</td></tr></table>`,
				__("Shortcuts")
			)
		);
	}

	toggle_kiosk() {
		$("body").toggleClass("ccp-kiosk");
		if ($("body").hasClass("ccp-kiosk")) {
			frappe.show_alert({ message: __("Full screen on. Press Esc twice or use the menu to exit."), indicator: "blue" });
			this.$root.find(".ccp-exit-kiosk").remove();
			this.$root.prepend(
				`<button class="ccp-btn sm ghost ccp-exit-kiosk" style="float:right">${__("Exit full screen")}</button>`
			);
			this.$root.find(".ccp-exit-kiosk").on("click", () => this.toggle_kiosk());
		} else {
			this.$root.find(".ccp-exit-kiosk").remove();
		}
	}

	bind_keys() {
		$(document).on("keydown.ccpos", (e) => {
			if (frappe.get_route_str() !== "car-cupid-pos") return;
			if (e.key === "F2") {
				e.preventDefault();
				this.$root.find(".ccp-scan").first().trigger("focus");
			} else if (e.key === "F4") {
				e.preventDefault();
				this.$root.find(".ccp-cust-search").first().trigger("focus");
			} else if (e.key === "F9") {
				e.preventDefault();
				this.$root.find(".ccp-pay-btn:enabled").first().trigger("click");
			} else if (e.key === "Escape" && $("body").hasClass("ccp-kiosk") && !$(".modal.show").length) {
				this._esc = (this._esc || 0) + 1;
				if (this._esc >= 2) {
					this._esc = 0;
					this.toggle_kiosk();
				}
				setTimeout(() => (this._esc = 0), 800);
			}
		});
	}

	async load_boot() {
		this.$root.html(`<div class="ccp-empty"><b>${__("Loading the counter…")}</b></div>`);
		try {
			this.boot = await this.call("car_cupid_service.api.pos.get_boot");
		} catch (e) {
			this.$root.html(`<div class="ccp-empty"><b>${__("Could not load the POS.")}</b>${__("Check your connection and reload the page.")}</div>`);
			return;
		}
		if (!this.boot.setup_ok) {
			this.$root.html(
				`<div class="ccp-empty"><b>${__("Car Cupid is not set up yet")}</b>
				${__("Add the three companies, their store and service bay warehouses and price lists in Car Cupid Settings.")}
				<div style="margin-top:12px"><a class="ccp-btn primary" href="/app/car-cupid-settings">${__("Open Car Cupid Settings")}</a></div></div>`
			);
			return;
		}
		this.sale = this.load_sale();
		this.render_shell();
		const start = this.boot.can.bill || this.boot.can.job ? "sale" : this.boot.can.store ? "stores" : "jobs";
		this.go(start);
		this.refresh_counts();
	}

	// ------------------------------------------------------------------ shell
	render_shell() {
		const tabs = [
			["sale", __("Counter sale")],
			["jobs", __("Job cards"), "ready"],
			["stores", __("Stores"), "awaiting"],
			["cash", __("Cash drawer")],
			["recovery", __("Recovery"), "failed"],
		].filter(([t]) => {
			if (t === "stores") return this.boot.can.store || this.boot.can.job;
			if (t === "cash") return this.boot.can.counter;
			if (t === "recovery") return this.boot.can.bill;
			return true;
		});
		this.$root.html(`
			<div class="ccp-top">
				<div class="ccp-tabs" role="tablist">
					${tabs
						.map(
							([t, l, badge]) =>
								`<button class="ccp-tab" role="tab" data-tab="${t}">${l}${badge ? `<span class="ccp-badge hidden" data-badge="${badge}"></span>` : ""}</button>`
						)
						.join("")}
				</div>
				<div class="ccp-spacer"></div>
				<button class="ccp-session" title="${__("Counter session")}"><span class="dot"></span><span class="txt"></span></button>
			</div>
			<div class="ccp-body"></div>`);
		this.$body = this.$root.find(".ccp-body");
		this.$root.find(".ccp-tab").on("click", (e) => this.go($(e.currentTarget).data("tab")));
		this.$root.find(".ccp-session").on("click", () => this.go("cash"));
		this.render_session_pill();
	}

	render_session_pill() {
		const open = !!this.boot.open_session;
		const $s = this.$root.find(".ccp-session").toggleClass("open", open);
		$s.find(".txt").text(open ? __("Counter open · {0}", [this.boot.open_session]) : __("Counter closed — open to take payments"));
	}

	go(tab) {
		this.tab = tab;
		this.$root.find(".ccp-tab").removeClass("active").filter(`[data-tab="${tab}"]`).addClass("active");
		const fn = { sale: "render_sale", jobs: "render_jobs", stores: "render_stores", cash: "render_cash", recovery: "render_recovery" }[tab];
		this[fn || "render_sale"]();
	}

	async refresh_counts() {
		try {
			const d = await this.call("car_cupid_service.api.pos.dashboard");
			this.counts = d || {};
			const set = (k, v) => {
				const $b = this.$root.find(`[data-badge="${k}"]`);
				$b.text(v || "").toggleClass("hidden", !v);
			};
			set("ready", d.ready_for_billing);
			set("awaiting", d.awaiting_issue);
			set("failed", d.failed_batches);
			this.boot.open_session = d.session ? d.session.name : null;
			this.render_session_pill();
		} catch (e) {
			/* counts are informational only */
		}
	}

	// ------------------------------------------------------------------ sale state
	new_sale() {
		return { client_request_id: car_cupid.uuid(), name: null, customer: this.boot.walk_in_customer || null, customer_name: null, vehicle: null, vehicles: [], items: [] };
	}

	load_sale() {
		try {
			const raw = localStorage.getItem(this.storage_key);
			if (raw) {
				const s = JSON.parse(raw);
				if (s && Array.isArray(s.items)) return s;
			}
		} catch (e) {
			/* storage unavailable — start fresh */
		}
		const s = this.new_sale();
		if (s.customer) this.fetch_customer_name(s);
		return s;
	}

	persist() {
		try {
			localStorage.setItem(this.storage_key, JSON.stringify(this.sale));
		} catch (e) {
			/* private window — the sale just won't survive a reload */
		}
	}

	async fetch_customer_name(s) {
		try {
			const r = await frappe.db.get_value("Customer", s.customer, "customer_name");
			s.customer_name = r.message && r.message.customer_name;
			if (this.tab === "sale") this.render_customer();
		} catch (e) {
			/* ignore */
		}
	}

	// ------------------------------------------------------------------ counter sale
	render_sale() {
		this.$body.html(`
			<div class="ccp-grid">
				<div>
					<div class="ccp-panel">
						<div class="ccp-scanwrap">${CCP_SEARCH_ICON}
							<input class="ccp-input scan ccp-scan" placeholder="${__("Scan barcode or type item / service name")}" autocomplete="off" inputmode="search">
						</div>
						<div class="ccp-row" style="margin-top:8px;justify-content:space-between">
							<span class="ccp-sub">${__("Each line is billed by the company shown on its chip.")}</span>
							<span class="ccp-sub"><span class="ccp-kbd">F2</span> ${__("search")} · <span class="ccp-kbd">F9</span> ${__("pay")}</span>
						</div>
						<div class="ccp-results"></div>
					</div>
					<div class="ccp-panel ccp-cart"></div>
				</div>
				<div>
					<div class="ccp-panel ccp-customer"></div>
					<div class="ccp-panel ccp-bill ccp-sticky-pay"></div>
				</div>
			</div>`);
		this.bind_scan(this.$body.find(".ccp-scan"), this.$body.find(".ccp-results"), (d) => this.add_to_sale(d));
		this.render_customer();
		this.render_cart();
		setTimeout(() => this.$body.find(".ccp-scan").trigger("focus"), 50);
	}

	bind_scan($input, $results, on_pick, customer_fn) {
		let timer = null;
		let last = [];
		const search = async (txt, exact_add) => {
			const customer = customer_fn ? customer_fn() : this.sale && this.sale.customer;
			let res = [];
			try {
				res = await this.call("car_cupid_service.api.pos.search_items", { txt, customer });
			} catch (e) {
				return;
			}
			last = res || [];
			if (exact_add && last.length === 1 && last[0].exact) {
				on_pick(last[0]);
				$input.val("");
				$results.empty();
				return;
			}
			this.render_results($results, last, on_pick, $input);
		};
		$input.on("input", () => {
			clearTimeout(timer);
			const txt = $input.val().trim();
			if (!txt) {
				$results.empty();
				return;
			}
			timer = setTimeout(() => search(txt, false), 250);
		});
		$input.on("keydown", async (e) => {
			if (e.key === "Enter") {
				e.preventDefault();
				clearTimeout(timer);
				const txt = $input.val().trim();
				if (!txt) return;
				// scanners type fast and press Enter: search fresh, add when exact
				await search(txt, true);
				if ($input.val() && last.length) {
					on_pick(last[0]);
					$input.val("");
					$results.empty();
				} else if ($input.val() && !last.length) {
					frappe.show_alert({ message: __("No item matches “{0}”", [txt]), indicator: "orange" });
					frappe.utils.play_sound("error");
				}
			} else if (e.key === "Escape") {
				$input.val("");
				$results.empty();
			}
		});
	}

	render_results($results, rows, on_pick, $input) {
		if (!rows.length) {
			$results.html(`<div class="ccp-empty" style="grid-column:1/-1"><b>${__("No matching items")}</b>${__("Check the spelling or scan the barcode again.")}</div>`);
			return;
		}
		$results.html(
			rows
				.map((d, i) => {
					const stock =
						d.is_stock_item && d.actual_qty != null
							? `<span class="ccp-pill ${d.actual_qty > 0 ? "ok" : "bad"}">${d.actual_qty > 0 ? __("{0} in stock", [d.actual_qty]) : __("Out of stock")}</span>`
							: `<span class="ccp-pill">${this.esc(d.item_type)}</span>`;
					return `<button class="ccp-result" data-i="${i}">
						<span class="nm">${this.esc(d.item_name)}</span>
						<span class="ccp-sub">${this.esc(d.item_code)}</span>
						<span class="meta">${this.chip(d.company)}<span class="price">${d.price_list_rate ? this.money(d.price_list_rate) : `<span class="ccp-pill bad">${__("No price")}</span>`}</span></span>
						<span>${stock}</span>
					</button>`;
				})
				.join("")
		);
		$results.find(".ccp-result").on("click", (e) => {
			on_pick(rows[$(e.currentTarget).data("i")]);
			$input.val("").trigger("focus");
			$results.empty();
		});
	}

	add_to_sale(d) {
		if (!d.price_list_rate && !this.boot.is_supervisor) {
			frappe.msgprint(__("{0} has no selling price for {1}. Ask a supervisor to add an Item Price.", [d.item_name, d.company]));
			return;
		}
		const same = this.sale.items.find((r) => r.item_code === d.item_code && r.company === d.company && !flt(r.discount_percentage));
		if (same) {
			same.qty = flt(same.qty) + 1;
		} else {
			this.sale.items.push({
				key: car_cupid.uuid(),
				item_code: d.item_code,
				item_name: d.item_name,
				item_type: d.item_type,
				is_stock_item: d.is_stock_item,
				company: d.company,
				uom: d.uom,
				qty: 1,
				price_list_rate: d.price_list_rate,
				discount_percentage: 0,
				sales_person: this.boot.sales_person || null,
				actual_qty: d.actual_qty,
			});
		}
		frappe.utils.play_sound("click");
		this.persist();
		this.render_cart();
	}

	group_by_company(items) {
		const groups = {};
		items.forEach((r) => (groups[r.company] = groups[r.company] || []).push(r));
		return groups;
	}

	line_amount(r) {
		return flt(r.price_list_rate) * (1 - flt(r.discount_percentage) / 100) * flt(r.qty);
	}

	render_cart() {
		const $c = this.$body.find(".ccp-cart");
		if (!this.sale.items.length) {
			$c.html(`<div class="ccp-empty"><b>${__("The cart is empty")}</b>${__("Scan a barcode or search above to add accessories and services.")}</div>`);
			this.render_bill();
			return;
		}
		const groups = this.group_by_company(this.sale.items);
		const sp_opts = (sel) =>
			`<option value="">${__("Salesperson…")}</option>` +
			(this.boot.sales_persons || []).map((s) => `<option ${s === sel ? "selected" : ""}>${this.esc(s)}</option>`).join("");
		const co_opts = (sel) => this.boot.companies.map((c) => `<option value="${this.esc(c.company)}" ${c.company === sel ? "selected" : ""}>${this.esc(c.label)}</option>`).join("");

		const html = Object.entries(groups)
			.map(([company, rows]) => {
				const co = this.company(company);
				const sub = rows.reduce((a, r) => a + this.line_amount(r), 0);
				return `<div class="ccp-lane" style="--lane:${co.color || "var(--gray-500)"}">
					<div class="ccp-lane-head"><span>${this.chip(company)} <b>${this.esc(co.company)}</b></span><b>${this.money(sub)}</b></div>
					${rows
						.map((r) => {
							const low = r.is_stock_item && r.actual_qty != null && flt(r.qty) > flt(r.actual_qty);
							const over = flt(r.discount_percentage) > flt(this.boot.max_discount) && !this.boot.is_supervisor;
							return `<div class="ccp-line" data-key="${r.key}">
								<div><div class="nm">${this.esc(r.item_name)}</div>
									<div class="ccp-sub">${this.esc(r.item_code)} · ${this.money(r.price_list_rate)} / ${this.esc(r.uom || "")}</div>
									${low ? `<div class="warn">${__("Only {0} in stock", [r.actual_qty])}</div>` : ""}
									${over ? `<div class="warn">${__("Discount above {0}% needs a supervisor", [this.boot.max_discount])}</div>` : ""}
								</div>
								<div class="amt">${this.money(this.line_amount(r))}</div>
								<div class="ctrls">
									<span class="ccp-step"><button data-act="minus" aria-label="${__("Less")}">−</button><input data-f="qty" value="${flt(r.qty)}" inputmode="decimal" aria-label="${__("Quantity")}"><button data-act="plus" aria-label="${__("More")}">+</button></span>
									<input class="ccp-mini disc" data-f="discount_percentage" value="${flt(r.discount_percentage) || ""}" placeholder="${__("Disc %")}" inputmode="decimal" aria-label="${__("Discount %")}">
									<select class="ccp-mini" data-f="sales_person" aria-label="${__("Salesperson")}">${sp_opts(r.sales_person)}</select>
									${this.boot.allow_company_override ? `<select class="ccp-mini" data-f="company" aria-label="${__("Company")}">${co_opts(r.company)}</select>` : ""}
									<button class="ccp-btn sm ghost danger" data-act="remove">${__("Remove")}</button>
								</div>
							</div>`;
						})
						.join("")}
				</div>`;
			})
			.join("");
		$c.html(`<div class="ccp-row" style="justify-content:space-between"><div class="ccp-h" style="margin:0">${__("Cart")} <span class="ccp-badge">${this.sale.items.length}</span></div>
			<button class="ccp-btn sm ghost danger ccp-clear">${__("Clear sale")}</button></div>${html}`);

		const find = (el) => this.sale.items.find((r) => r.key === $(el).closest(".ccp-line").data("key"));
		$c.find("[data-act]").on("click", (e) => {
			const r = find(e.currentTarget);
			const act = $(e.currentTarget).data("act");
			if (act === "plus") r.qty = flt(r.qty) + 1;
			if (act === "minus") r.qty = Math.max(1, flt(r.qty) - 1);
			if (act === "remove") this.sale.items = this.sale.items.filter((x) => x !== r);
			this.persist();
			this.render_cart();
		});
		$c.find("[data-f]").on("change", async (e) => {
			const r = find(e.currentTarget);
			const f = $(e.currentTarget).data("f");
			let v = $(e.currentTarget).val();
			if (f === "qty") v = Math.max(flt(v), 0.001);
			if (f === "discount_percentage") v = Math.min(Math.max(flt(v), 0), 100);
			r[f] = v;
			if (f === "company") {
				const d = await this.call("car_cupid_service.api.pos.get_line", { item_code: r.item_code, company: v, customer: this.sale.customer });
				r.price_list_rate = d.price_list_rate;
				r.actual_qty = d.actual_qty;
			}
			this.persist();
			this.render_cart();
		});
		$c.find(".ccp-clear").on("click", () =>
			frappe.confirm(__("Remove every line and start a new sale?"), () => {
				this.sale = this.new_sale();
				if (this.sale.customer) this.fetch_customer_name(this.sale);
				this.persist();
				this.render_sale();
			})
		);
		this.render_bill();
	}

	render_bill() {
		const $b = this.$body.find(".ccp-bill");
		const groups = this.group_by_company(this.sale.items);
		const total = this.sale.items.reduce((a, r) => a + this.line_amount(r), 0);
		const split = Object.entries(groups)
			.map(([c, rows]) => {
				const amt = rows.reduce((a, r) => a + this.line_amount(r), 0);
				const co = this.company(c);
				return { co, amt };
			})
			.filter((x) => x.amt > 0);
		const no_session = !this.boot.open_session;
		const need_vehicle = this.boot.vehicle_required_for_pos && !this.sale.vehicle;
		const can_pay = this.sale.items.length && this.sale.customer && !no_session && !need_vehicle && this.boot.can.bill;
		let hint = "";
		if (!this.boot.can.bill) hint = __("Save the cart for a cashier — you can't collect payments.");
		else if (no_session) hint = __("Open the cash drawer before taking payment.");
		else if (!this.sale.customer) hint = __("Choose a customer first.");
		else if (need_vehicle) hint = __("Select the customer's vehicle first.");

		$b.html(`
			<div class="ccp-h">${__("Bill")}</div>
			<div class="ccp-split" aria-label="${__("Share of each company in this bill")}">
				${split.map((x) => `<span title="${this.esc(x.co.label)} ${this.money(x.amt)}" style="width:${total ? (x.amt * 100) / total : 0}%;background:${x.co.color || "var(--gray-500)"}"></span>`).join("")}
			</div>
			${split.map((x) => `<div class="ccp-billrow"><span>${this.chip(x.co.company)} ${this.esc(x.co.company)}</span><span>${this.money(x.amt)}</span></div>`).join("") || `<div class="ccp-sub">${__("Lines will be grouped by company here.")}</div>`}
			<div class="ccp-billrow total"><span>${__("Total before tax")}</span><span>${this.money(total)}</span></div>
			<div class="ccp-note">${__("GST is worked out per company when you collect payment. Each company gets its own tax invoice.")}</div>
			<div style="margin-top:12px" class="ccp-row">
				<button class="ccp-btn big primary ccp-pay-btn" ${can_pay ? "" : "disabled"}>${__("Collect payment")}</button>
				${this.sale.items.length ? `<button class="ccp-btn ccp-save-draft" style="width:100%">${__("Save as draft memo")}</button>` : ""}
			</div>
			${hint ? `<div class="ccp-note" style="color:var(--ccp-warn)">${hint}</div>` : ""}`);
		$b.find(".ccp-pay-btn").on("click", () => this.checkout_sale());
		$b.find(".ccp-save-draft").on("click", async () => {
			const memo = await this.save_memo(true);
			if (memo) frappe.show_alert({ message: __("Saved as {0}", [memo.name]), indicator: "green" });
		});
	}

	// ------------------------------------------------------------------ customer & vehicle
	render_customer() {
		const $c = this.$body.find(".ccp-customer");
		if (!$c.length) return;
		const s = this.sale;
		const initials = (s.customer_name || s.customer || "?").split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();
		$c.html(`
			<div class="ccp-h">${__("Customer")}</div>
			${
				s.customer
					? `<div class="ccp-cust"><div class="ccp-avatar">${this.esc(initials)}</div>
						<div style="flex:1"><div style="font-weight:700">${this.esc(s.customer_name || s.customer)}</div>
						<div class="ccp-sub">${this.esc(s.mobile_no || (s.customer === this.boot.walk_in_customer ? __("Walk-in") : ""))}</div></div>
						<button class="ccp-btn sm ccp-change-cust">${__("Change")}</button></div>`
					: ""
			}
			<div class="ccp-cust-pick ${s.customer ? "hidden" : ""}" style="margin-top:8px">
				<input class="ccp-input ccp-cust-search" placeholder="${__("Name, mobile or vehicle number")}" autocomplete="off">
				<div class="ccp-cust-results"></div>
				<div class="ccp-row" style="margin-top:8px">
					<button class="ccp-btn sm ccp-new-cust">${__("New customer")}</button>
					${this.boot.walk_in_customer ? `<button class="ccp-btn sm ccp-walkin">${__("Walk-in")}</button>` : ""}
				</div>
			</div>
			${s.customer ? `<div class="ccp-vehicles">${(s.vehicles || []).map((v) => `<button class="ccp-vehicle ${v.name === s.vehicle ? "sel" : ""}" data-v="${this.esc(v.name)}">${this.esc(v.name)}</button>`).join("")}
				<button class="ccp-btn sm ghost ccp-add-vehicle">+ ${__("Vehicle")}</button></div>` : ""}`);
		$c.find(".ccp-change-cust").on("click", () => {
			$c.find(".ccp-cust-pick").removeClass("hidden");
			$c.find(".ccp-cust-search").trigger("focus");
		});
		$c.find(".ccp-walkin").on("click", () => this.set_customer({ name: this.boot.walk_in_customer, customer_name: __("Walk-in customer"), vehicles: [] }));
		$c.find(".ccp-new-cust").on("click", () => this.dialog_new_customer((c) => this.set_customer(c)));
		$c.find(".ccp-vehicle").on("click", (e) => {
			const v = $(e.currentTarget).data("v");
			this.sale.vehicle = this.sale.vehicle === v ? null : v;
			this.persist();
			this.render_customer();
			this.render_bill();
		});
		$c.find(".ccp-add-vehicle").on("click", () =>
			this.dialog_vehicle(this.sale.customer, (v) => {
				this.sale.vehicles = [...(this.sale.vehicles || []).filter((x) => x.name !== v.name), { name: v.name }];
				this.sale.vehicle = v.name;
				this.persist();
				this.render_customer();
				this.render_bill();
			})
		);
		this.bind_customer_search($c.find(".ccp-cust-search"), $c.find(".ccp-cust-results"), (c) => this.set_customer(c));
	}

	bind_customer_search($input, $out, on_pick) {
		let timer;
		$input.on("input", () => {
			clearTimeout(timer);
			const txt = $input.val().trim();
			if (txt.length < 2) return $out.empty();
			timer = setTimeout(async () => {
				const rows = await this.call("car_cupid_service.api.customer.search", { txt });
				if (!rows.length) {
					$out.html(`<div class="ccp-sub" style="padding:8px 2px">${__("No customer found. Use “New customer”.")}</div>`);
					return;
				}
				$out.html(
					`<div class="ccp-dropdown">${rows
						.map(
							(c, i) => `<button data-i="${i}"><b>${this.esc(c.customer_name)}</b> <span class="ccp-sub">${this.esc(c.mobile_no || "")}</span>
							<div class="ccp-sub">${(c.vehicles || []).map((v) => this.esc(v.name)).join(", ")}</div></button>`
						)
						.join("")}</div>`
				);
				$out.find("button").on("click", (e) => on_pick(rows[$(e.currentTarget).data("i")]));
			}, 250);
		});
	}

	set_customer(c) {
		this.sale.customer = c.name;
		this.sale.customer_name = c.customer_name;
		this.sale.mobile_no = c.mobile_no;
		this.sale.vehicles = c.vehicles || [];
		this.sale.vehicle = c.matched_vehicle || (this.sale.vehicles.length === 1 ? this.sale.vehicles[0].name : null);
		this.sale.name = null; // customer change => fresh memo on save
		this.persist();
		this.render_customer();
		this.render_bill();
		this.$body.find(".ccp-scan").trigger("focus");
	}

	dialog_new_customer(done) {
		const d = new frappe.ui.Dialog({
			title: __("New customer"),
			fields: [
				{ fieldname: "customer_name", label: __("Full name"), fieldtype: "Data", reqd: 1 },
				{ fieldname: "mobile_no", label: __("Mobile number"), fieldtype: "Data", options: "Phone", reqd: 1, description: __("10 digits. If this number is already registered, that customer is used.") },
				{ fieldname: "email_id", label: __("Email (optional)"), fieldtype: "Data", options: "Email" },
			],
			primary_action_label: __("Add customer"),
			primary_action: async (v) => {
				const c = await this.call("car_cupid_service.api.customer.quick_create", v, true);
				d.hide();
				if (c.existing) frappe.show_alert({ message: __("Mobile already registered to {0} — using that customer", [c.customer_name]), indicator: "blue" });
				const found = await this.call("car_cupid_service.api.customer.search", { txt: c.customer_name });
				done((found || []).find((x) => x.name === c.name) || { ...c, vehicles: [] });
			},
		});
		d.show();
	}

	dialog_vehicle(customer, done) {
		const d = new frappe.ui.Dialog({
			title: __("Add vehicle"),
			fields: [
				{ fieldname: "vehicle_number", label: __("Registration number"), fieldtype: "Data", reqd: 1, description: __("e.g. MH12AB1234 — spaces are removed automatically") },
				{ fieldname: "make", label: __("Make"), fieldtype: "Data" },
				{ fieldname: "model", label: __("Model"), fieldtype: "Data" },
				{ fieldname: "colour", label: __("Colour"), fieldtype: "Data" },
				{ fieldname: "fuel_type", label: __("Fuel"), fieldtype: "Select", options: "\nPetrol\nDiesel\nCNG\nElectric\nHybrid" },
			],
			primary_action_label: __("Save vehicle"),
			primary_action: async (v) => {
				const r = await this.call("car_cupid_service.api.vehicle.search_or_create", { ...v, customer }, true);
				if (r.owner_mismatch) {
					frappe.confirm(
						`${this.esc(r.message)}<br><br>${__("Transfer this vehicle to the current customer?")}`,
						async () => {
							const t = await this.call("car_cupid_service.api.vehicle.transfer_owner", { vehicle: r.name, customer }, true);
							d.hide();
							done(t);
						}
					);
					return;
				}
				d.hide();
				done(r);
			},
		});
		d.show();
	}

	// ------------------------------------------------------------------ memo + checkout
	memo_payload() {
		return {
			name: this.sale.name,
			client_request_id: this.sale.client_request_id,
			customer: this.sale.customer,
			vehicle: this.sale.vehicle,
			counter_session: this.boot.open_session,
			items: this.sale.items.map((r) => ({
				name: r.row_name || null,
				item_code: r.item_code,
				company: r.company,
				qty: r.qty,
				uom: r.uom,
				price_list_rate: r.price_list_rate,
				discount_percentage: r.discount_percentage,
				sales_person: r.sales_person,
				item_type: r.item_type,
			})),
		};
	}

	async save_memo(freeze) {
		try {
			const memo = await this.call("car_cupid_service.api.pos.save_cash_memo", { data: this.memo_payload() }, freeze);
			this.sale.name = memo.name;
			// keep server row names so approvals/edits stay attached to the same line
			memo.items.forEach((row, i) => this.sale.items[i] && (this.sale.items[i].row_name = row.name));
			this.persist();
			return memo;
		} catch (e) {
			return null;
		}
	}

	async checkout_sale() {
		const memo = await this.save_memo(true);
		if (!memo) return;
		let preview;
		try {
			preview = await this.call("car_cupid_service.api.pos.preview_bill", { source_doctype: "Counter Cash Memo", source_name: memo.name }, true);
		} catch (e) {
			return;
		}
		this.payment_dialog(preview, async (payments, request_key, allow_credit) => {
			const res = await this.call(
				"car_cupid_service.api.pos.checkout_cash_memo",
				{ data: this.memo_payload(), payments, request_key, allow_credit: allow_credit ? 1 : 0 },
				true
			);
			this.sale = this.new_sale();
			if (this.sale.customer) this.fetch_customer_name(this.sale);
			this.persist();
			return res;
		});
	}

	payment_dialog(preview, submit) {
		// The request key is created once per dialog: a double tap or a retry after a timeout
		// reaches the server with the same key and gets the same batch back.
		const request_key = car_cupid.uuid();
		const total = flt(preview.grand_total);
		const modes = preview.modes && preview.modes.length ? preview.modes : this.boot.modes || [];
		if (!modes.length) {
			frappe.msgprint(__("No payment mode has an account for all companies on this bill. Set the accounts in Mode of Payment."));
			return;
		}
		const cash = this.boot.cash_mode;
		const state = {};
		modes.forEach((m) => (state[m.name] = { amount: 0, ref: "" }));
		if (state[cash]) state[cash].amount = total;

		const d = new frappe.ui.Dialog({ title: __("Collect payment"), size: "large", fields: [{ fieldtype: "HTML", fieldname: "body" }] });
		const $w = d.fields_dict.body.$wrapper.addClass("ccp ccp-pay");

		const render = () => {
			const paid = Object.values(state).reduce((a, s) => a + flt(s.amount), 0);
			const cash_paid = flt((state[cash] || {}).amount);
			const diff = flt(paid - total, 2);
			const non_cash_over = diff > 0 && diff > cash_paid + 0.005;
			const missing_ref = modes.some((m) => m.type === "Bank" && flt(state[m.name].amount) > 0 && !state[m.name].ref.trim());
			let status;
			if (diff < -0.5) status = `<div class="status short"><span>${__("Still to collect")}</span><span>${this.money(-diff)}</span></div>`;
			else if (non_cash_over) status = `<div class="status short"><span>${__("Card / UPI more than the bill")}</span><span>${this.money(diff)}</span></div>`;
			else if (diff > 0.005) status = `<div class="status ok"><span>${__("Give change")}</span><span>${this.money(diff)}</span></div>`;
			else status = `<div class="status ok"><span>${__("Exact amount")}</span><span>✓</span></div>`;
			const credit_ok = this.boot.allow_credit_sale && this.boot.is_supervisor;
			const ok = !non_cash_over && !missing_ref && (diff >= -0.5 || (credit_ok && $w.find(".ccp-credit").is(":checked")));

			$w.find(".ccp-status").html(status + (missing_ref ? `<div class="ccp-err" style="margin-top:6px">${__("Enter the UPI / card transaction reference.")}</div>` : ""));
			d.get_primary_btn().prop("disabled", !ok);
		};

		$w.html(`
			<div>${(preview.companies || [])
				.map((c) => `<div class="ccp-inv" style="--lane:${c.color || "var(--gray-500)"}"><span>${this.chip(c.company)} <b>${this.esc(c.company)}</b>
					<span class="ccp-sub">${__("{0} line(s) · tax {1}", [c.lines, this.money(c.tax)])}</span></span><b>${this.money(c.grand_total)}</b></div>`)
				.join("")}</div>
			${(preview.blockers || []).length ? `<div class="ccp-note" style="color:var(--ccp-warn);margin-top:6px">${__("Billed now; the rest stays on the job card:")}<br>${preview.blockers.map((b) => this.esc(b)).join("<br>")}</div>` : ""}
			<div class="ccp-billrow total"><span>${__("Amount payable")}</span><span>${this.money(total)}</span></div>
			<div class="quick">
				<button class="ccp-btn sm" data-q="exact">${__("Exact cash")}</button>
				${[100, 500, 2000].map((n) => `<button class="ccp-btn sm" data-q="${n}">${__("Round up to {0}", [n])}</button>`).join("")}
				<button class="ccp-btn sm" data-q="clear">${__("Clear")}</button>
			</div>
			<div class="modes">${modes
				.map(
					(m) => `<div class="mode"><label>${this.esc(m.name)}</label>
					<input class="ccp-input" data-m="${this.esc(m.name)}" data-k="amount" inputmode="decimal" value="${flt(state[m.name].amount) || ""}">
					${m.type === "Bank" ? `<input class="ccp-input" style="margin-top:6px" data-m="${this.esc(m.name)}" data-k="ref" placeholder="${__("Txn / UTR ref")}">` : ""}
					</div>`
				)
				.join("")}</div>
			${this.boot.allow_credit_sale && this.boot.is_supervisor ? `<label class="ccp-row" style="margin-top:10px"><input type="checkbox" class="ccp-credit"> ${__("Bill the unpaid balance on credit")}</label>` : ""}
			<div class="ccp-status"></div>`);

		$w.find("[data-m]").on("input", (e) => {
			const $i = $(e.currentTarget);
			state[$i.data("m")][$i.data("k")] = $i.data("k") === "amount" ? flt($i.val()) : $i.val();
			render();
		});
		$w.find(".ccp-credit").on("change", render);
		$w.find("[data-q]").on("click", (e) => {
			const q = $(e.currentTarget).data("q");
			const other = Object.entries(state).filter(([k]) => k !== cash).reduce((a, [, s]) => a + flt(s.amount), 0);
			const due = Math.max(total - other, 0);
			if (!state[cash]) return;
			if (q === "clear") Object.values(state).forEach((s) => (s.amount = 0));
			else if (q === "exact") state[cash].amount = due;
			else state[cash].amount = Math.ceil(due / q) * q;
			$w.find("[data-k=amount]").each((i, el) => $(el).val(flt(state[$(el).data("m")].amount) || ""));
			render();
		});

		let busy = false;
		d.set_primary_action(__("Confirm & create invoices"), async () => {
			if (busy) return;
			busy = true;
			d.get_primary_btn().prop("disabled", true);
			const payments = Object.entries(state)
				.filter(([, s]) => flt(s.amount) > 0)
				.map(([m, s]) => ({ mode_of_payment: m, amount: flt(s.amount), reference_no: s.ref }));
			try {
				const res = await submit(payments, request_key, $w.find(".ccp-credit").is(":checked"));
				d.hide();
				this.show_result(res);
			} catch (e) {
				// the server already rolled back or the key will return the existing batch on retry
				busy = false;
				render();
			}
		});
		d.show();
		render();
		setTimeout(() => $w.find(`[data-m="${cash}"][data-k=amount]`).trigger("select"), 150);
	}

	show_result(res) {
		this.boot.open_session = this.boot.open_session || null;
		this.refresh_counts();
		const ok = res.status === "Submitted";
		const print = (dt, name, format) =>
			window.open(frappe.urllib.get_full_url(`/printview?doctype=${encodeURIComponent(dt)}&name=${encodeURIComponent(name)}&trigger_print=1${format ? `&format=${encodeURIComponent(format)}` : ""}`));
		const d = new frappe.ui.Dialog({ title: ok ? __("Sale complete") : __("Some invoices need attention"), size: "large", fields: [{ fieldtype: "HTML", fieldname: "b" }] });
		const $w = d.fields_dict.b.$wrapper.addClass("ccp");
		$w.html(`
			<div class="ccp-done ${ok ? "" : "partial"}"><div class="tick">${ok ? "✓" : "!"}</div>
				<div style="font-size:18px;font-weight:800">${ok ? __("{0} invoices created", [res.companies.length]) : __("{0} of {1} invoices created", [res.companies.filter((c) => c.status === "Submitted").length, res.companies.length])}</div>
				${flt(res.change_due) ? `<div style="font-size:22px;font-weight:800;margin-top:8px">${__("Change to return")}: ${this.money(res.change_due)}</div>` : ""}
				${flt(res.outstanding_amount) > 0.5 ? `<div class="ccp-note" style="color:var(--ccp-warn)">${__("Unpaid balance")}: ${this.money(res.outstanding_amount)}</div>` : ""}
			</div>
			${res.companies
				.map((c) => {
					const co = this.company(c.company);
					return `<div class="ccp-inv" style="--lane:${co.color || "var(--gray-500)"}"><div>${this.chip(c.company)} <b>${this.esc(c.sales_invoice || c.company)}</b>
						<div class="ccp-sub">${this.money(c.grand_total)} · ${this.esc(c.status)}</div>${c.error ? `<div class="ccp-err">${this.esc(c.error)}</div>` : ""}</div>
						${c.sales_invoice ? `<button class="ccp-btn sm" data-si="${this.esc(c.sales_invoice)}">${__("Print invoice")}</button>` : ""}</div>`;
				})
				.join("")}
			<div class="ccp-row" style="margin-top:14px">
				<button class="ccp-btn primary ccp-memo">${__("Print customer memo")}</button>
				${!ok ? `<button class="ccp-btn ccp-retry">${__("Retry failed invoices")}</button>` : ""}
				<button class="ccp-btn ccp-next">${__("Next customer")}</button>
			</div>`);
		$w.find("[data-si]").on("click", (e) => print("Sales Invoice", $(e.currentTarget).data("si")));
		$w.find(".ccp-memo").on("click", () => print("Invoice Batch", res.batch, "Car Cupid Consolidated Memo"));
		$w.find(".ccp-retry").on("click", async () => {
			const r = await this.call("car_cupid_service.api.pos.retry_batch", { batch: res.batch }, true);
			d.hide();
			this.show_result(r);
		});
		$w.find(".ccp-next").on("click", () => {
			d.hide();
			this.go(this.tab);
		});
		d.show();
		if (this.tab === "sale") this.render_sale();
		else if (this.current_job) this.open_job(this.current_job);
	}

	// ------------------------------------------------------------------ job cards
	async render_jobs(filter) {
		this.current_job = null;
		this.job_filter = filter || this.job_filter || "active";
		const c = this.counts || {};
		const kpi = (k, v, l, f, alert) =>
			`<button class="ccp-kpi ${alert && v ? "alert" : ""}" data-f="${f}"><div class="v">${v == null ? "–" : v}</div><div class="l">${l}</div></button>`;
		this.$body.html(`
			<div class="ccp-kpis">
				${kpi("o", c.open_jobs, __("In the workshop"), "active")}
				${kpi("r", c.ready_for_billing, __("Ready to bill"), "Ready for Billing")}
				${kpi("b", c.billed_not_delivered, __("Billed, awaiting delivery"), "Billed")}
				${kpi("d", c.overdue, __("Past promised time"), "active", true)}
			</div>
			<div class="ccp-panel">
				<div class="ccp-row" style="justify-content:space-between">
					<div class="ccp-filters">
						${[["active", __("Active")], ["Open", __("Open")], ["Work In Progress", __("In progress")], ["Ready for Billing", __("Ready to bill")], ["Billed", __("Billed")], ["all", __("All")]]
							.map(([f, l]) => `<button class="ccp-btn sm ${this.job_filter === f ? "on" : ""}" data-jf="${f}">${l}</button>`)
							.join("")}
					</div>
					<button class="ccp-btn primary ccp-new-job">+ ${__("New job card")}</button>
				</div>
				<input class="ccp-input ccp-job-search" placeholder="${__("Search vehicle number, customer or mobile")}" style="margin-bottom:8px">
				<div class="ccp-job-list"><div class="ccp-empty">${__("Loading…")}</div></div>
			</div>`);
		this.$body.find("[data-jf], .ccp-kpi").on("click", (e) => this.render_jobs($(e.currentTarget).data("jf") || $(e.currentTarget).data("f")));
		this.$body.find(".ccp-new-job").on("click", () => this.dialog_new_job());
		let t;
		this.$body.find(".ccp-job-search").on("input", (e) => {
			clearTimeout(t);
			t = setTimeout(() => this.load_job_list($(e.currentTarget).val()), 250);
		});
		this.load_job_list();
	}

	async load_job_list(txt) {
		const rows = await this.call("car_cupid_service.api.job_card.list_jobs", { status: this.job_filter, txt });
		const $l = this.$body.find(".ccp-job-list");
		if (!rows.length) {
			$l.html(`<div class="ccp-empty"><b>${__("No job cards here")}</b>${__("Create one with “New job card”.")}</div>`);
			return;
		}
		const pill = (s) => {
			const cls = { "Ready for Billing": "ok", Billed: "ok", Delivered: "", Cancelled: "bad", "Partially Billed": "warn", "Work In Progress": "warn" }[s] || "";
			return `<span class="ccp-pill ${cls}">${this.esc(__(s))}</span>`;
		};
		const now = frappe.datetime.now_datetime();
		$l.html(`<table class="ccp-table"><thead><tr><th>${__("Vehicle")}</th><th>${__("Customer")}</th><th>${__("Status")}</th><th>${__("Stores")}</th><th>${__("Promised")}</th><th style="text-align:right">${__("Value")}</th></tr></thead><tbody>
			${rows
				.map((r) => {
					const late = r.promised_delivery && r.promised_delivery < now && !["Billed", "Delivered", "Cancelled"].includes(r.status);
					return `<tr class="click" data-j="${this.esc(r.name)}"><td><b>${this.esc(r.vehicle_number)}</b><div class="ccp-sub">${this.esc(r.name)}${r.priority !== "Normal" ? ` · <span class="ccp-pill warn">${this.esc(r.priority)}</span>` : ""}</div></td>
						<td>${this.esc(r.customer_name)}<div class="ccp-sub">${this.esc(r.vehicle_description || "")}</div></td>
						<td>${pill(r.status)}</td><td><span class="ccp-pill">${this.esc(__(r.fulfilment_status || ""))}</span></td>
						<td>${r.promised_delivery ? `<span class="${late ? "ccp-err" : ""}">${frappe.datetime.str_to_user(r.promised_delivery)}</span>` : "–"}</td>
						<td style="text-align:right">${this.money(r.net_total)}</td></tr>`;
				})
				.join("")}</tbody></table>`);
		$l.find("tr[data-j]").on("click", (e) => this.open_job($(e.currentTarget).data("j")));
	}

	dialog_new_job() {
		let customer = null;
		const d = new frappe.ui.Dialog({
			title: __("New job card — vehicle intake"),
			size: "large",
			fields: [
				{ fieldname: "vehicle_number", label: __("Vehicle number"), fieldtype: "Data", reqd: 1, description: __("Type the registration; known vehicles fill in the owner") },
				{ fieldname: "vh", fieldtype: "HTML" },
				{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer", reqd: 1 },
				{ fieldname: "new_cust", fieldtype: "Button", label: __("New customer") },
				{ fieldtype: "Column Break" },
				{ fieldname: "odometer", label: __("Odometer (km)"), fieldtype: "Int" },
				{ fieldname: "fuel_level", label: __("Fuel level"), fieldtype: "Select", options: "\nEmpty\n1/4\n1/2\n3/4\nFull" },
				{ fieldname: "promised_delivery", label: __("Promised delivery"), fieldtype: "Datetime" },
				{ fieldname: "priority", label: __("Priority"), fieldtype: "Select", options: "Normal\nUrgent\nVIP", default: "Normal" },
				{ fieldtype: "Section Break" },
				{ fieldname: "customer_complaint", label: __("What does the customer want?"), fieldtype: "Small Text", reqd: 1 },
				{ fieldtype: "Column Break" },
				{ fieldname: "damage_notes", label: __("Existing scratches / damage"), fieldtype: "Small Text" },
				{ fieldname: "belongings_noted", label: __("Belongings left in the car"), fieldtype: "Small Text" },
				{ fieldtype: "Section Break" },
				{ fieldname: "default_technician", label: __("Technician"), fieldtype: "Link", options: "Employee" },
				{ fieldtype: "Column Break" },
				{ fieldname: "customer_acknowledged", label: __("Customer agreed to the intake condition"), fieldtype: "Check" },
			],
			primary_action_label: __("Open job card"),
			primary_action: async (v) => {
				let vehicle;
				try {
					vehicle = await this.call("car_cupid_service.api.vehicle.search_or_create", { vehicle_number: v.vehicle_number, customer: v.customer }, true);
				} catch (e) {
					return;
				}
				if (vehicle.owner_mismatch) {
					frappe.msgprint(vehicle.message);
					return;
				}
				const res = await this.call("car_cupid_service.api.job_card.create", { data: { ...v, vehicle: vehicle.name } }, true);
				if (res.duplicate) {
					frappe.confirm(__("This vehicle already has an open job card {0}. Open it instead?", [res.name]), () => {
						d.hide();
						this.open_job(res.name);
					});
					return;
				}
				d.hide();
				this.open_job(res.name);
			},
		});
		d.fields_dict.new_cust.$input.on("click", () => this.dialog_new_customer((c) => d.set_value("customer", c.name)));
		d.fields_dict.vehicle_number.$input.on("change", async () => {
			const num = d.get_value("vehicle_number");
			if (!num) return;
			const v = await this.call("car_cupid_service.api.vehicle.lookup", { vehicle_number: num });
			const $vh = d.fields_dict.vh.$wrapper;
			if (v) {
				customer = v.customer;
				d.set_value("customer", v.customer);
				$vh.html(`<div class="ccp" style="margin:-4px 0 8px"><span class="ccp-pill ok">${__("Known vehicle")}</span> <b>${frappe.utils.escape_html(v.customer_name || "")}</b>
					<span class="ccp-sub">${frappe.utils.escape_html([v.make, v.model, v.colour].filter(Boolean).join(" "))}${v.last_odometer ? ` · ${__("last odometer")} ${v.last_odometer}` : ""}</span>
					${v.open_job_card ? `<div class="ccp-err">${__("Open job card already exists: {0}", [v.open_job_card])}</div>` : ""}</div>`);
			} else {
				$vh.html(`<div class="ccp"><span class="ccp-pill">${__("New vehicle — it will be registered to the customer below")}</span></div>`);
			}
		});
		d.show();
	}

	async open_job(name) {
		this.tab = "jobs";
		this.$root.find(".ccp-tab").removeClass("active").filter('[data-tab="jobs"]').addClass("active");
		this.current_job = name;
		let jc;
		try {
			jc = await this.call("car_cupid_service.api.job_card.get", { job_card: name }, true);
		} catch (e) {
			return this.render_jobs();
		}
		this.render_job(jc);
	}

	render_job(jc) {
		this.current_job = jc.name;
		const closed = ["Delivered", "Cancelled"].includes(jc.status);
		const groups = this.group_by_company(jc.items || []);
		const fpill = (s) => `<span class="ccp-pill ${{ Issued: "ok", "Partially Issued": "warn", Requested: "warn", Pending: "bad" }[s] || ""}">${this.esc(__(s || ""))}</span>`;
		const wpill = (s) => `<span class="ccp-pill ${{ Completed: "ok", "In Progress": "warn", Cancelled: "bad" }[s] || ""}">${this.esc(__(s || ""))}</span>`;
		const tech_opts = (sel) =>
			`<option value="">${__("Technician…")}</option>` + (this.boot.technicians || []).map((t) => `<option value="${this.esc(t.name)}" ${t.name === sel ? "selected" : ""}>${this.esc(t.employee_name)}</option>`).join("");
		const sp_opts = (sel) => `<option value="">${__("Salesperson…")}</option>` + (this.boot.sales_persons || []).map((s) => `<option ${s === sel ? "selected" : ""}>${this.esc(s)}</option>`).join("");

		const lanes = Object.entries(groups)
			.map(([company, rows]) => {
				const co = this.company(company);
				return `<div class="ccp-lane" style="--lane:${co.color || "var(--gray-500)"}">
					<div class="ccp-lane-head"><span>${this.chip(company)} <b>${this.esc(company)}</b></span><b>${this.money(rows.reduce((a, r) => a + flt(r.amount), 0))}</b></div>
					${rows
						.map((r) => {
							const locked = flt(r.billed_qty) >= flt(r.qty);
							return `<div class="ccp-line" data-row="${this.esc(r.name)}">
								<div><div class="nm">${this.esc(r.item_name)} ${r.work_status === "Cancelled" ? `<span class="ccp-pill bad">${__("Cancelled")}</span>` : ""}</div>
									<div class="ccp-sub">${this.esc(r.item_code)} · ${flt(r.qty)} × ${this.money(r.rate)}${flt(r.discount_percentage) ? ` (−${flt(r.discount_percentage)}%)` : ""}
									${r.is_stock_item ? ` · ${__("issued")} ${flt(r.issued_qty) - flt(r.returned_qty)}/${flt(r.qty)}` : ""}${flt(r.billed_qty) ? ` · ${__("billed")} ${flt(r.billed_qty)}` : ""}</div>
									<div class="ccp-row" style="margin-top:4px">${r.is_stock_item ? fpill(r.fulfilment_status) : wpill(r.work_status)}</div>
								</div>
								<div class="amt">${this.money(r.amount)}</div>
								${
									closed || locked || r.work_status === "Cancelled"
										? ""
										: `<div class="ctrls">
									<span class="ccp-step"><button data-act="minus">−</button><input data-f="qty" value="${flt(r.qty)}" inputmode="decimal"><button data-act="plus">+</button></span>
									<input class="ccp-mini disc" data-f="discount_percentage" value="${flt(r.discount_percentage) || ""}" placeholder="${__("Disc %")}" inputmode="decimal">
									<select class="ccp-mini" data-f="sales_person">${sp_opts(r.sales_person)}</select>
									${r.is_stock_item ? "" : `<select class="ccp-mini" data-f="technician">${tech_opts(r.technician)}</select>
									<select class="ccp-mini" data-f="work_status">${["Pending", "In Progress", "Completed"].map((s) => `<option ${s === r.work_status ? "selected" : ""} value="${s}">${__(s)}</option>`).join("")}</select>`}
									<button class="ccp-btn sm ghost danger" data-act="${flt(r.requested_qty) || flt(r.issued_qty) ? "cancel" : "remove"}">${flt(r.requested_qty) || flt(r.issued_qty) ? __("Cancel line") : __("Remove")}</button>
								</div>`
								}
							</div>`;
						})
						.join("")}
				</div>`;
			})
			.join("");

		const unrequested = (jc.items || []).some((r) => r.is_stock_item && r.work_status !== "Cancelled" && flt(r.qty) > flt(r.requested_qty));
		const services_open = (jc.items || []).some((r) => !r.is_stock_item && !["Completed", "Cancelled"].includes(r.work_status));
		const can_bill = this.boot.can.bill && !closed && jc.billing_status !== "Fully Billed" && (jc.items || []).length;

		this.$body.html(`
			<div class="ccp-row" style="margin-bottom:10px"><button class="ccp-btn sm ghost ccp-back">← ${__("All job cards")}</button></div>
			<div class="ccp-grid">
				<div>
					<div class="ccp-panel">
						<div class="ccp-row" style="justify-content:space-between">
							<div><div style="font-size:22px;font-weight:800;letter-spacing:.03em">${this.esc(jc.vehicle_number)}</div>
							<div class="ccp-sub">${this.esc(jc.vehicle_description || "")} · ${this.esc(jc.name)}</div></div>
							<span class="ccp-pill ${["Ready for Billing", "Billed"].includes(jc.status) ? "ok" : "warn"}" style="font-size:13px">${this.esc(__(jc.status))}</span>
						</div>
						${jc.customer_complaint ? `<div style="margin-top:8px"><span class="ccp-sub">${__("Customer asked for")}:</span> ${this.esc(jc.customer_complaint)}</div>` : ""}
					</div>
					${closed ? "" : `<div class="ccp-panel"><div class="ccp-scanwrap">${CCP_SEARCH_ICON}<input class="ccp-input scan ccp-scan" placeholder="${__("Add part, accessory or service to this job")}"></div><div class="ccp-results"></div></div>`}
					<div class="ccp-panel">${lanes || `<div class="ccp-empty"><b>${__("No work added yet")}</b>${__("Search above to add parts and services.")}</div>`}</div>
				</div>
				<div>
					<div class="ccp-panel">
						<div class="ccp-h">${__("Customer")}</div>
						<div style="font-weight:700">${this.esc(jc.customer_name)}</div>
						<div class="ccp-sub">${[jc.mobile_no ? this.esc(jc.mobile_no) : "", jc.odometer ? `${jc.odometer} km` : "", jc.fuel_level ? `${__("fuel")} ${this.esc(jc.fuel_level)}` : ""].filter(Boolean).join(" · ")}</div>
						${jc.promised_delivery ? `<div class="ccp-sub">${__("Promised")}: ${frappe.datetime.str_to_user(jc.promised_delivery)}</div>` : ""}
					</div>
					<div class="ccp-panel">
						<div class="ccp-h">${__("Next steps")}</div>
						<div class="ccp-row" style="flex-direction:column;align-items:stretch">
							${!closed && unrequested ? `<button class="ccp-btn ccp-req">${__("Send parts request to stores")}</button>` : ""}
							${!closed && services_open ? `<button class="ccp-btn ccp-done-all">${__("Mark all work completed")}</button>` : ""}
							${can_bill ? `<button class="ccp-btn big primary ccp-pay-btn">${jc.status === "Ready for Billing" || jc.status === "Partially Billed" ? __("Bill & collect payment") : __("Check what can be billed")}</button>` : ""}
							${jc.status === "Billed" ? `<button class="ccp-btn primary ccp-deliver">${__("Hand over vehicle")}</button>` : ""}
							<button class="ccp-btn ccp-print">${__("Print job card")}</button>
							<button class="ccp-btn ghost ccp-form">${__("Open full form")}</button>
							${!closed && !flt(jc.total_billed_amount) ? `<button class="ccp-btn ghost danger ccp-cancel">${__("Cancel job")}</button>` : ""}
						</div>
						${jc.estimated_incentive ? `<div class="ccp-note">${__("Estimated incentive on this job")}: ${this.money(jc.estimated_incentive)}</div>` : ""}
					</div>
					<div class="ccp-panel">
						<div class="ccp-h">${__("Totals")}</div>
						${(jc.company_summary || []).map((c) => `<div class="ccp-billrow"><span>${this.chip(c.company)}</span><span>${this.money(c.net_total)}</span></div>`).join("")}
						<div class="ccp-billrow total"><span>${__("Total before tax")}</span><span>${this.money(jc.net_total)}</span></div>
						${(jc.invoices || []).map((i) => `<div class="ccp-inv"><span>${this.chip(i.company)} ${this.esc(i.sales_invoice)}</span><span>${this.money(i.grand_total)} <span class="ccp-pill">${this.esc(i.status)}</span></span></div>`).join("")}
					</div>
				</div>
			</div>`);

		const $b = this.$body;
		$b.find(".ccp-back").on("click", () => this.render_jobs());
		$b.find(".ccp-form").on("click", () => frappe.set_route("Form", "Service Job Card", jc.name));
		$b.find(".ccp-print").on("click", () =>
			window.open(frappe.urllib.get_full_url(`/printview?doctype=Service%20Job%20Card&name=${encodeURIComponent(jc.name)}&format=${encodeURIComponent("Car Cupid Job Card")}&trigger_print=1`))
		);
		const reload = (p) => p.then((r) => r && this.render_job(r)).catch(() => {});
		$b.find(".ccp-req").on("click", () => reload(this.call("car_cupid_service.api.job_card.request_materials", { job_card: jc.name }, true)));
		$b.find(".ccp-done-all").on("click", () => reload(this.call("car_cupid_service.api.job_card.set_work_status", { job_card: jc.name, status: "Completed" }, true)));
		$b.find(".ccp-deliver").on("click", () =>
			frappe.confirm(__("Hand over {0} to the customer?", [jc.vehicle_number]), () => reload(this.call("car_cupid_service.api.job_card.mark_delivered", { job_card: jc.name }, true)))
		);
		$b.find(".ccp-cancel").on("click", () =>
			frappe.prompt({ fieldname: "reason", label: __("Why is the job cancelled?"), fieldtype: "Small Text", reqd: 1 }, (v) =>
				reload(this.call("car_cupid_service.api.job_card.cancel_job", { job_card: jc.name, reason: v.reason }, true))
			, __("Cancel job"), __("Cancel job"))
		);
		$b.find(".ccp-pay-btn").on("click", () => this.checkout_job(jc));

		const row_of = (el) => $(el).closest(".ccp-line").data("row");
		const row_data = (el) => (jc.items || []).find((r) => r.name === row_of(el));
		$b.find(".ccp-line [data-act]").on("click", (e) => {
			const act = $(e.currentTarget).data("act");
			const r = row_data(e.currentTarget);
			if (act === "remove") return reload(this.call("car_cupid_service.api.job_card.remove_line", { job_card: jc.name, row: r.name }, true));
			if (act === "cancel")
				return frappe.confirm(__("Cancel this line? Issued parts must be returned to stores first."), () =>
					reload(this.call("car_cupid_service.api.job_card.set_work_status", { job_card: jc.name, rows: [r.name], status: "Cancelled" }, true))
				);
			const qty = act === "plus" ? flt(r.qty) + 1 : Math.max(1, flt(r.qty) - 1);
			reload(this.call("car_cupid_service.api.job_card.update_line", { job_card: jc.name, row: r.name, values: { qty } }));
		});
		$b.find(".ccp-line [data-f]").on("change", (e) => {
			const f = $(e.currentTarget).data("f");
			let v = $(e.currentTarget).val();
			if (f === "qty" || f === "discount_percentage") v = flt(v);
			reload(this.call("car_cupid_service.api.job_card.update_line", { job_card: jc.name, row: row_of(e.currentTarget), values: { [f]: v } }));
		});
		if (!closed) {
			this.bind_scan(
				$b.find(".ccp-scan"),
				$b.find(".ccp-results"),
				(d) =>
					reload(
						this.call(
							"car_cupid_service.api.job_card.add_line",
							{ job_card: jc.name, line: { item_code: d.item_code, company: d.company, qty: 1, item_type: d.item_type, sales_person: jc.default_sales_person || this.boot.sales_person } },
							true
						)
					),
				() => jc.customer
			);
		}
	}

	async checkout_job(jc) {
		let preview;
		try {
			preview = await this.call("car_cupid_service.api.pos.preview_bill", { source_doctype: "Service Job Card", source_name: jc.name }, true);
		} catch (e) {
			return;
		}
		if (!preview.can_bill) {
			frappe.msgprint({
				title: __("Not ready to bill yet"),
				indicator: "orange",
				message: `${__("Finish these first")}:<br><br>${(preview.blockers || []).map((b) => `• ${frappe.utils.escape_html(b)}`).join("<br>")}`,
			});
			return;
		}
		if (!this.boot.open_session) {
			frappe.msgprint(__("Open the cash drawer first (Cash drawer tab) so the payment is counted in today's session."));
			return;
		}
		this.payment_dialog(preview, (payments, request_key, allow_credit) =>
			this.call("car_cupid_service.api.job_card.checkout", { job_card: jc.name, payments, request_key, allow_credit: allow_credit ? 1 : 0 }, true)
		);
	}

	// ------------------------------------------------------------------ stores
	async render_stores() {
		this.$body.html(`<div class="ccp-panel"><div class="ccp-row" style="justify-content:space-between">
			<div class="ccp-h" style="margin:0">${__("Parts waiting to be issued")}</div>
			<div class="ccp-row"><select class="ccp-mini ccp-st-co"><option value="">${__("All companies")}</option>${this.boot.companies.map((c) => `<option value="${this.esc(c.company)}">${this.esc(c.label)}</option>`).join("")}</select>
			<label class="ccp-row ccp-sub"><input type="checkbox" class="ccp-st-issued"> ${__("Show issued (for returns)")}</label></div></div>
			<div class="ccp-st-list" style="margin-top:10px"><div class="ccp-empty">${__("Loading…")}</div></div></div>`);
		const load = async () => {
			const rows = await this.call("car_cupid_service.api.inventory.pending_requests", {
				company: this.$body.find(".ccp-st-co").val(),
				include_issued: this.$body.find(".ccp-st-issued").is(":checked") ? 1 : 0,
			});
			const $l = this.$body.find(".ccp-st-list");
			if (!rows.length) {
				$l.html(`<div class="ccp-empty"><b>${__("Nothing waiting")}</b>${__("New parts requests from job cards show up here.")}</div>`);
				return;
			}
			const byJob = {};
			rows.forEach((r) => (byJob[r.job_card] = byJob[r.job_card] || []).push(r));
			$l.html(
				Object.entries(byJob)
					.map(
						([job, items]) => `<div class="ccp-lane" style="--lane:var(--ccp-rose)">
					<div class="ccp-lane-head"><span><b>${this.esc(items[0].vehicle_number || "")}</b> <span class="ccp-sub">${this.esc(job)} · ${this.esc(items[0].customer_name || "")}</span></span>
					${this.boot.can.store && items.some((i) => i.pending_qty > 0) ? `<button class="ccp-btn sm primary" data-issue-all="${this.esc(job)}">${__("Issue all")}</button>` : ""}</div>
					${items
						.map(
							(r) => `<div class="ccp-line" data-mir="${this.esc(r.name)}"><div><div class="nm">${this.esc(r.item_name)}</div>
						<div class="ccp-sub">${this.chip(r.company)} ${this.esc(r.source_warehouse)} → ${this.esc(r.target_warehouse)}</div>
						<div class="ccp-row" style="margin-top:4px"><span class="ccp-pill">${__("requested")} ${flt(r.requested_qty)}</span><span class="ccp-pill ok">${__("issued")} ${flt(r.issued_qty) - flt(r.returned_qty)}</span>
						<span class="ccp-pill ${r.in_stock >= r.pending_qty ? "" : "bad"}">${__("in store")} ${flt(r.in_stock)}</span></div></div>
						<div class="amt">${flt(r.pending_qty) ? `${flt(r.pending_qty)} ${this.esc(r.uom)}` : "✓"}</div>
						${this.boot.can.store ? `<div class="ctrls">${r.pending_qty > 0 ? `<button class="ccp-btn sm primary" data-issue>${__("Issue")}</button>` : ""}
						${flt(r.issued_qty) - flt(r.returned_qty) > 0 ? `<button class="ccp-btn sm" data-return>${__("Take back")}</button>` : ""}
						${r.status === "Requested" ? `<button class="ccp-btn sm ghost danger" data-cancel>${__("Cancel request")}</button>` : ""}</div>` : ""}
						</div>`
						)
						.join("")}</div>`
					)
					.join("")
			);
			const mir = (el) => rows.find((r) => r.name === $(el).closest(".ccp-line").data("mir"));
			$l.find("[data-issue]").on("click", (e) => {
				const r = mir(e.currentTarget);
				frappe.prompt(
					[
						{ fieldname: "qty", label: __("Quantity to issue"), fieldtype: "Float", default: r.pending_qty, reqd: 1 },
						{ fieldname: "received_by", label: __("Handed to"), fieldtype: "Link", options: "Employee", default: r.technician },
					],
					async (v) => {
						await this.call("car_cupid_service.api.inventory.record_issue", { mir: r.name, qty: v.qty, received_by: v.received_by }, true);
						frappe.show_alert({ message: __("Issued {0} {1}", [v.qty, r.item_name]), indicator: "green" });
						load();
						this.refresh_counts();
					},
					__("Issue {0}", [r.item_name]),
					__("Issue")
				);
			});
			$l.find("[data-return]").on("click", (e) => {
				const r = mir(e.currentTarget);
				frappe.prompt(
					[
						{ fieldname: "qty", label: __("Quantity coming back"), fieldtype: "Float", default: 1, reqd: 1 },
						{ fieldname: "remarks", label: __("Reason"), fieldtype: "Data" },
					],
					async (v) => {
						await this.call("car_cupid_service.api.inventory.record_return", { mir: r.name, qty: v.qty, remarks: v.remarks }, true);
						frappe.show_alert({ message: __("Returned to stores"), indicator: "green" });
						load();
					},
					__("Take back {0}", [r.item_name]),
					__("Take back")
				);
			});
			$l.find("[data-cancel]").on("click", (e) => {
				const r = mir(e.currentTarget);
				frappe.confirm(__("Cancel the request for {0}?", [r.item_name]), async () => {
					await this.call("car_cupid_service.api.inventory.cancel_request", { mir: r.name }, true);
					load();
				});
			});
			$l.find("[data-issue-all]").on("click", async (e) => {
				const res = await this.call("car_cupid_service.api.inventory.issue_all", { job_card: $(e.currentTarget).data("issue-all") }, true);
				if (res.skipped.length)
					frappe.msgprint({ title: __("Some parts were not issued"), indicator: "orange", message: res.skipped.map((s) => `• ${frappe.utils.escape_html(s.item_code)}: ${frappe.utils.escape_html(s.reason)}`).join("<br>") });
				else frappe.show_alert({ message: __("All parts issued"), indicator: "green" });
				load();
				this.refresh_counts();
			});
		};
		this.$body.find(".ccp-st-co, .ccp-st-issued").on("change", load);
		load();
	}

	// ------------------------------------------------------------------ cash drawer
	async render_cash() {
		this.$body.html(`<div class="ccp-empty">${__("Loading…")}</div>`);
		let s = null;
		try {
			s = await this.call("car_cupid_service.api.counter.get_session");
		} catch (e) {
			/* handled below */
		}
		this.boot.open_session = s ? s.name : null;
		this.render_session_pill();
		if (!s) {
			this.$body.html(`<div class="ccp-panel" style="max-width:520px">
				<div class="ccp-h">${__("Open the cash drawer")}</div>
				<div class="ccp-sub" style="margin-bottom:10px">${__("Count the float you are starting with. Every payment you take is recorded against this session until you close it.")}</div>
				<label class="ccp-sub">${__("Counter")}</label><input class="ccp-input ccp-cn" value="${__("Main Counter")}">
				<label class="ccp-sub" style="margin-top:8px;display:block">${__("Opening float (cash in drawer)")}</label><input class="ccp-input ccp-fl" inputmode="decimal" value="0">
				<button class="ccp-btn big primary" style="margin-top:12px">${__("Open drawer")}</button></div>`);
			this.$body.find(".ccp-btn.primary").on("click", async () => {
				const r = await this.call("car_cupid_service.api.counter.open_session", { counter_name: this.$body.find(".ccp-cn").val(), opening_float: flt(this.$body.find(".ccp-fl").val()) }, true);
				this.boot.open_session = r.name;
				frappe.show_alert({ message: __("Drawer open"), indicator: "green" });
				this.render_cash();
			});
			return;
		}
		const counts = {};
		(s.closing_denominations || []).forEach((d) => (counts[String(flt(d.denomination))] = d.count || 0));
		const denoms = this.boot.denominations || [];
		this.$body.html(`<div class="ccp-grid">
			<div class="ccp-panel">
				<div class="ccp-h">${__("Close the drawer — count the cash")}</div>
				<div class="ccp-denoms">${denoms.map((d) => `<label class="ccp-denom"><b>${format_number(d, null, 0)}</b><input data-d="${flt(d)}" inputmode="numeric" value="${counts[String(flt(d))] || ""}" placeholder="0"></label>`).join("")}</div>
				<div class="ccp-stat big" style="margin-top:12px"><span>${__("Counted")}</span><span class="ccp-counted">${this.money(0)}</span></div>
				<div class="ccp-stat big"><span>${__("Difference")}</span><span class="ccp-var">${this.money(0)}</span></div>
				<input class="ccp-input ccp-reason" placeholder="${__("Reason for any difference")}" value="${this.esc(s.variance_reason || "")}" style="margin-top:8px">
				${s.is_supervisor ? `<label class="ccp-row" style="margin-top:8px"><input type="checkbox" class="ccp-approve"> ${__("I approve this difference")}</label>` : ""}
				<button class="ccp-btn big primary ccp-close" style="margin-top:12px">${__("Close drawer")}</button>
				${s.status === "Pending Review" ? `<div class="ccp-note" style="color:var(--ccp-warn)">${__("Waiting for a supervisor to approve the difference.")}</div>` : ""}
			</div>
			<div>
				<div class="ccp-panel">
					<div class="ccp-h">${this.esc(s.counter_name)} <span class="ccp-pill ok">${this.esc(s.name)}</span></div>
					<div class="ccp-stat"><span>${__("Opening float")}</span><b>${this.money(s.opening_float)}</b></div>
					<div class="ccp-stat"><span>${__("Cash taken")}</span><b>${this.money(s.cash_receipts)}</b></div>
					<div class="ccp-stat"><span>${__("Cash in")}</span><b>${this.money(s.cash_in)}</b></div>
					<div class="ccp-stat"><span>${__("Cash out / refunds / deposits")}</span><b>− ${this.money(s.cash_out)}</b></div>
					<div class="ccp-stat big"><span>${__("Should be in drawer")}</span><span>${this.money(s.expected_cash)}</span></div>
					<div class="ccp-stat"><span>${__("UPI / card / bank (not in drawer)")}</span><b>${this.money(s.non_cash_receipts)}</b></div>
					${(s.mode_summary || []).map((m) => `<div class="ccp-stat ccp-sub"><span>${this.esc(m.mode_of_payment)} · ${m.transactions}</span><span>${this.money(m.amount)}</span></div>`).join("")}
				</div>
				<div class="ccp-panel">
					<div class="ccp-h">${__("Cash in / out")}</div>
					<div class="ccp-row"><button class="ccp-btn sm" data-mv="Cash In">${__("Cash in")}</button><button class="ccp-btn sm" data-mv="Cash Out">${__("Pay out")}</button>
					<button class="ccp-btn sm" data-mv="Refund">${__("Refund")}</button><button class="ccp-btn sm" data-mv="Bank Deposit">${__("Bank deposit")}</button></div>
					${(s.movements || []).map((m) => `<div class="ccp-stat ccp-sub"><span>${this.esc(m.movement_type)} · ${this.esc(m.reason)}</span><span>${this.money(m.amount)}</span></div>`).join("")}
				</div>
			</div></div>`);
		const tolerance = flt(s.tolerance);
		const recalc = () => {
			let total = 0;
			this.$body.find("[data-d]").each((i, el) => (total += flt($(el).data("d")) * cint($(el).val())));
			const variance = total - flt(s.expected_cash);
			this.$body.find(".ccp-counted").text(this.money(total));
			this.$body.find(".ccp-var").text(this.money(variance)).css("color", Math.abs(variance) > tolerance ? "var(--ccp-bad)" : "var(--ccp-ok)");
			return { total, variance };
		};
		this.$body.find("[data-d]").on("input", recalc);
		recalc();
		this.$body.find("[data-mv]").on("click", (e) => {
			const type = $(e.currentTarget).data("mv");
			frappe.prompt(
				[
					{ fieldname: "amount", label: __("Amount"), fieldtype: "Currency", reqd: 1 },
					{ fieldname: "reason", label: __("Reason"), fieldtype: "Data", reqd: 1 },
				],
				async (v) => {
					await this.call("car_cupid_service.api.counter.add_movement", { session: s.name, movement_type: type, amount: v.amount, reason: v.reason }, true);
					this.render_cash();
				},
				__(type),
				__("Record")
			);
		});
		this.$body.find(".ccp-close").on("click", () => {
			const { total, variance } = recalc();
			const countsOut = {};
			this.$body.find("[data-d]").each((i, el) => (countsOut[String($(el).data("d"))] = cint($(el).val())));
			const reason = this.$body.find(".ccp-reason").val();
			if (Math.abs(variance) > tolerance && !reason) {
				frappe.msgprint(__("The drawer is off by {0}. Write down the reason before closing.", [this.money(variance)]));
				return;
			}
			frappe.confirm(__("Close the drawer with {0} counted?", [this.money(total)]), async () => {
				const r = await this.call(
					"car_cupid_service.api.counter.close_session",
					{ session: s.name, counts: countsOut, variance_reason: reason, approve_variance: this.$body.find(".ccp-approve").is(":checked") ? 1 : 0 },
					true
				);
				if (r.needs_approval) {
					frappe.msgprint({ title: __("Sent for approval"), indicator: "orange", message: __("The difference of {0} needs a supervisor's approval. The drawer stays open until then.", [this.money(r.variance)]) });
					this.render_cash();
					return;
				}
				frappe.show_alert({ message: __("Drawer closed"), indicator: "green" });
				window.open(frappe.urllib.get_full_url(`/printview?doctype=Counter%20Session&name=${encodeURIComponent(s.name)}&format=${encodeURIComponent("Car Cupid Counter Closing")}&trigger_print=1`));
				this.boot.open_session = null;
				this.render_cash();
			});
		});
	}

	// ------------------------------------------------------------------ recovery
	async render_recovery() {
		const rows = await frappe.db.get_list("Invoice Batch", {
			filters: { status: ["in", ["Failed", "Partially Submitted", "In Progress"]] },
			fields: ["name", "status", "source_doctype", "source_name", "customer_name", "total_amount", "modified"],
			order_by: "modified desc",
			limit: 50,
		});
		if (!rows.length) {
			this.$body.html(`<div class="ccp-panel"><div class="ccp-empty"><b>${__("Nothing to fix")}</b>${__("Every invoice batch went through.")}</div></div>`);
			return;
		}
		this.$body.html(`<div class="ccp-panel"><div class="ccp-h">${__("Invoice batches that need attention")}</div>
			<div class="ccp-sub" style="margin-bottom:8px">${__("Retry only re-creates the invoices that failed. Invoices already created are never duplicated.")}</div>
			<table class="ccp-table"><thead><tr><th>${__("Batch")}</th><th>${__("For")}</th><th>${__("Status")}</th><th></th></tr></thead><tbody>
			${rows.map((r) => `<tr><td><b>${this.esc(r.name)}</b><div class="ccp-sub">${frappe.datetime.str_to_user(r.modified)}</div></td>
				<td>${this.esc(r.customer_name)}<div class="ccp-sub">${this.esc(r.source_name)}</div></td>
				<td><span class="ccp-pill ${r.status === "Failed" ? "bad" : "warn"}">${this.esc(r.status)}</span></td>
				<td style="text-align:right"><button class="ccp-btn sm" data-view="${this.esc(r.name)}">${__("Details")}</button>
				<button class="ccp-btn sm primary" data-retry="${this.esc(r.name)}">${__("Retry")}</button></td></tr>`).join("")}
			</tbody></table></div>`);
		this.$body.find("[data-view]").on("click", async (e) => this.show_result(await this.call("car_cupid_service.api.pos.get_batch", { batch: $(e.currentTarget).data("view") })));
		this.$body.find("[data-retry]").on("click", async (e) => {
			try {
				const r = await this.call("car_cupid_service.api.pos.retry_batch", { batch: $(e.currentTarget).data("retry") }, true);
				this.show_result(r);
			} finally {
				this.render_recovery();
			}
		});
	}
}
