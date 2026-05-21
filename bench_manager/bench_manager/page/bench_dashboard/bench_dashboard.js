/**
 * Bench Dashboard - Main Frappe Page
 * Provides a full GUI for managing Frappe Bench operations.
 * Uses frappe.call() for API communication and frappe.realtime for live output.
 */

frappe.pages['bench-dashboard'].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Bench Manager',
		single_column: true,
	});

	new BenchDashboard(page);
};

class BenchDashboard {
	constructor(page) {
		this.page = page;
		this.console_logs = [];
		this.pending_ops = 0;
		this._poll_timer = null;
		this.init();
	}

	// Track background operations and poll for updates
	start_pending_op() {
		this.pending_ops++;
		if (!this._poll_timer) {
			this._poll_timer = setInterval(() => {
				// Check if counts changed before doing full refresh
				frappe.call({
					method: 'bench_manager.api.get_bench_status',
					async: true,
					callback: (r) => {
						if (!r.message) return;
						const d = r.message;
						const curApps = this.$container.find('#status-apps-count').text();
						const curSites = this.$container.find('#status-sites-count').text();
						if (String(d.apps_count) !== curApps || String(d.sites_count) !== curSites) {
							this.$container.find('#status-apps-count').text(d.apps_count);
							this.$container.find('#status-sites-count').text(d.sites_count);
							this.load_apps();
							this.load_sites();
						}
					}
				});
			}, 6000);
		}
		// Safety: auto-stop polling after 120s even if no realtime event fires
		setTimeout(() => {
			this.stop_pending_op();
		}, 120000);
	}

	stop_pending_op() {
		this.pending_ops = Math.max(0, this.pending_ops - 1);
		if (this.pending_ops === 0 && this._poll_timer) {
			clearInterval(this._poll_timer);
			this._poll_timer = null;
			// Final refresh
			setTimeout(() => {
				this.load_apps();
				this.load_sites();
				this.load_status();
			}, 1500);
		}
	}

	init() {
		this.page.main.html(frappe.render_template('bench_dashboard'));
		this.$container = this.page.main.find('.bench-dashboard');
		this.setup_tabs();
		this.setup_realtime();
		this.setup_site_actions();
		this.setup_app_actions();
		this.setup_bench_actions();
		this.setup_log_actions();
		this.setup_benches_actions();
		this.setup_vscode_actions();
		this.load_status();
		this.load_benches();
		this.populate_context_selector();
	}

	// ─── Tab Management ──────────────────────────────────────────

	setup_tabs() {
		const self = this;
		let current_img = 1;
		
		this.$container.find('#btn-toggle-menu').on('click', function() {
			self.$container.find('.bench-tabs').toggleClass('show');
		});

		this.$container.find('.bench-tab').on('click', function () {
			self.$container.find('.bench-tabs').removeClass('show');
			const tab = $(this).data('tab');
			self.$container.find('.bench-tab').removeClass('active');
			$(this).addClass('active');
			self.$container.find('.tab-pane').removeClass('active');
			self.$container.find(`#tab-${tab}`).addClass('active');

			// Toggle header illustration
			if (current_img === 1) {
				self.$container.find('#header-ill-1').css({ 'opacity': '0', 'transform': 'translateY(15px) scale(0.9)', 'pointer-events': 'none' });
				self.$container.find('#header-ill-2').css({ 'opacity': '1', 'transform': 'translateY(0) scale(1)', 'pointer-events': 'auto' });
				current_img = 2;
			} else {
				self.$container.find('#header-ill-1').css({ 'opacity': '1', 'transform': 'translateY(0) scale(1)', 'pointer-events': 'auto' });
				self.$container.find('#header-ill-2').css({ 'opacity': '0', 'transform': 'translateY(15px) scale(0.9)', 'pointer-events': 'none' });
				current_img = 1;
			}

			// Load data for selected tab
			if (tab === 'sites') self.load_sites();
			else if (tab === 'apps') self.load_apps();
			else if (tab === 'benches') self.load_benches();
			else if (tab === 'bench') self.load_bench_info();
			else if (tab === 'logs') self.load_logs();
			else if (tab === 'vscode') self.load_vscode_instances();
		});
	}

	// ─── Real-time ───────────────────────────────────────────────

	append_console(message, type = 'stdout') {
		const time = frappe.datetime.now_time().split('.')[0];
		this.console_logs.push({ time, message, type });
		if (this.console_logs.length > 500) {
			this.console_logs.shift(); // Keep last 500
		}

		if (this.live_activity_dialog && this.live_activity_dialog.$wrapper.is(':visible')) {
			const $console = this.live_activity_dialog.$wrapper.find('#live-activity-output');
			const cssClass = `console-${type}`;
			
			const $line = $(`<div class="console-line ${cssClass}"><span class="log-time" style="color: #858585; margin-right: 15px;">${time}</span><span class="log-msg"></span></div>`);
			$line.find('.log-msg').text(message);
			
			$console.append($line);
			const terminal = this.live_activity_dialog.$wrapper.find('.terminal-container')[0];
			if (terminal) {
				terminal.scrollTop = terminal.scrollHeight;
			}
		}
	}

	setup_realtime() {
		// 1. Socket.IO realtime (existing — works when websocket is healthy)
		frappe.realtime.on('bench_console', (data) => {
			if (data && data.message) {
				this.append_console(data.message, data.msg_type || 'stdout');
				// Command completed — refresh everything & decrement pending
				if (data.msg_type === 'success' || data.msg_type === 'error') {
					if (this.live_activity_dialog && this.live_activity_dialog.$wrapper.is(':visible')) {
						const statusColor = data.msg_type === 'success' ? '#12B76A' : '#F04438';
						const statusText = data.msg_type === 'success' ? '✓ Completed' : '✕ Failed';
						this.live_activity_dialog.set_title(`Live Activity <span style="color: ${statusColor}; font-size: 13px; font-weight: 600; margin-left: 10px;">${statusText}</span>`);
					}
					this.stop_pending_op();
					this._stop_sse_polling();
					setTimeout(() => {
						this.load_sites();
						this.load_apps();
						this.load_status();
					}, 1500);
				}
			}
		});
	}

	// 2. SSE polling for zero-latency live logs (no Socket.IO dependency)
	_start_sse_polling() {
		if (this._sse_timer) return; // Already polling
		this._sse_last_id = this._sse_last_id || 0;

		this._sse_timer = setInterval(() => {
			frappe.call({
				method: 'bench_manager.api.get_sse_events',
				args: { last_id: this._sse_last_id },
				async: true,
				callback: (r) => {
					if (!r.message || !r.message.events) return;
					const events = r.message.events;
					this._sse_last_id = r.message.last_id;

					events.forEach((evt) => {
						// Deduplicate: only append if not already in console_logs
						const isDuplicate = this.console_logs.some(
							l => l.message === evt.message && l.type === evt.msg_type
							&& Math.abs(new Date('1970-01-01T' + l.time) - new Date('1970-01-01T' + evt.time)) < 2000
						);
						if (!isDuplicate) {
							this.append_console(evt.message, evt.msg_type);
						}

						// Handle completion events
						if (evt.msg_type === 'success' || evt.msg_type === 'error') {
							if (this.live_activity_dialog && this.live_activity_dialog.$wrapper.is(':visible')) {
								const statusColor = evt.msg_type === 'success' ? '#12B76A' : '#F04438';
								const statusText = evt.msg_type === 'success' ? '✓ Completed' : '✕ Failed';
								this.live_activity_dialog.set_title(`Live Activity <span style="color: ${statusColor}; font-size: 13px; font-weight: 600; margin-left: 10px;">${statusText}</span>`);
							}
							this.stop_pending_op();
							this._stop_sse_polling();
							setTimeout(() => {
								this.load_sites();
								this.load_apps();
								this.load_status();
							}, 1500);
						}
					});
				}
			});
		}, 500); // Poll every 500ms for near-realtime
	}

	_stop_sse_polling() {
		if (this._sse_timer) {
			clearInterval(this._sse_timer);
			this._sse_timer = null;
		}
	}

	// ─── Status Bar ──────────────────────────────────────────────

	load_status() {
		frappe.call({
			method: 'bench_manager.api.get_bench_status',
			callback: (r) => {
				if (r.message) {
					const d = r.message;
					this.$container.find('#status-benches-count').text(d.benches_count || '1');
					this.$container.find('#status-sites-count').text(d.sites_count);
					this.$container.find('#status-apps-count').text(d.apps_count);
				}
			},
		});
	}

	// ─── Context Selector ───────────────────────────────────────

	populate_context_selector() {
		const $select = this.$container.find('#bench-context-select');
		const $indicator = this.$container.find('#bench-context-indicator');
		const $indicatorName = this.$container.find('#bench-context-name');

		frappe.call({
			method: 'bench_manager.api.discover_benches',
			callback: (r) => {
				const benches = r.message || [];
				this.benches = benches; // Save for dialogs
				$select.html('');
				if (benches.length === 0) {
					$select.append(`<option value="">No benches found</option>`);
					return;
				}
				
				let hostBench = null;
				benches.forEach((b) => {
					if (b.is_host) hostBench = b;
					const label = b.is_host ? `${b.name} (host)` : b.name;
					const selected = b.is_host ? 'selected' : '';
					$select.append(`<option value="${frappe.utils.escape_html(b.path)}" data-name="${frappe.utils.escape_html(b.name)}" data-ishost="${b.is_host}" ${selected}>${frappe.utils.escape_html(label)}</option>`);
				});

				// Set initial context
				if (hostBench) {
					this.current_bench_path = hostBench.path;
					this.current_bench_name = hostBench.name;
					this.is_host_bench = true;
				}

				// Handle context switch
				$select.on('change', () => {
					const $opt = $select.find('option:selected');
					this.current_bench_path = $opt.val();
					this.current_bench_name = $opt.data('name');
					this.is_host_bench = $opt.data('ishost') === true;

					if (!this.is_host_bench) {
						$indicator.show();
						$indicatorName.text(this.current_bench_name);
					} else {
						$indicator.hide();
					}

					// Reload active tab
					const activeTab = this.$container.find('.bench-tab.active').data('tab');
					if (activeTab === 'sites') this.load_sites();
					else if (activeTab === 'apps') this.load_apps();
					else if (activeTab === 'bench') this.load_bench_info();
				});
			}
		});
	}

	// ─── Benches Tab ──────────────────────────────────────────────

	setup_benches_actions() {
		const self = this;
		this.$container.find('#btn-init-bench, #btn-add-new-bench').on('click', () => {
			self.show_init_bench_dialog();
		});
	}

	show_init_bench_dialog() {
		const self = this;

		const d = new frappe.ui.Dialog({
			title: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:6px;vertical-align:-3px;"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg> Initialize New Bench`,
			fields: [
				{
					fieldtype: 'HTML',
					options: `<div style="padding:12px 0 8px; margin-bottom:8px; border-bottom:1px solid var(--border-color);">
						<p style="margin:0; color:var(--text-muted); font-size:13px; line-height:1.6;">
							This will create a new Frappe bench directory at <code>/home/praveen/</code>. 
							The process downloads Frappe, sets up a Python virtual environment, and installs all dependencies.
							<strong>This typically takes 5–10 minutes.</strong>
						</p>
					</div>`
				},
				{
					label: 'Bench Name',
					fieldname: 'bench_name',
					fieldtype: 'Data',
					reqd: 1,
					placeholder: 'e.g., frappe-bench-dev',
					description: 'A folder with this name will be created at <code>/home/praveen/</code>',
				},
				{
					label: 'Frappe Version',
					fieldname: 'frappe_branch',
					fieldtype: 'Select',
					options: 'version-15',
					default: 'version-15',
					description: 'Currently limited to version-15 as Python 3.12 is not available',
				},
				{
					fieldtype: 'HTML',
					options: `<div style="display:flex; gap:10px; align-items:center; padding:10px 14px; background:var(--bg-light-gray, #f8fafc); border-radius:10px; border:1px solid var(--border-color); margin-top:4px;">
						<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--primary)" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>
						<div style="flex:1; font-size:12px; color:var(--text-muted);">
							<strong>Python:</strong> Auto-detected from current system<br>
							<strong>Path:</strong> /home/praveen/
						</div>
					</div>`
				},
				{ fieldtype: 'Section Break', label: 'Also Create a Site (Optional)', collapsible: 1, collapsible_depends_on: 'eval:false' },
				{
					label: 'Site Name',
					fieldname: 'site_name',
					fieldtype: 'Data',
					placeholder: 'e.g., mysite.localhost',
					description: 'Leave blank to skip. The site will be created after bench initialization completes.',
				},
				{
					label: 'Admin Password',
					fieldname: 'admin_password',
					fieldtype: 'Password',
					default: 'admin',
					description: 'Password for the Administrator user of the new site',
					depends_on: 'eval:doc.site_name',
				},
				{
					label: 'MariaDB Root Password',
					fieldname: 'mariadb_root_password',
					fieldtype: 'Password',
					description: 'Required if MariaDB root has a password set',
					depends_on: 'eval:doc.site_name',
				},
			],
			size: 'small',
			primary_action_label: 'Initialize Bench',
			primary_action(values) {
				if (!values.bench_name) {
					frappe.msgprint('Bench name is required');
					return;
				}

				// Validate bench name
				if (!/^[a-zA-Z0-9._-]+$/.test(values.bench_name)) {
					frappe.msgprint('Bench name can only contain letters, numbers, dots, hyphens, and underscores.');
					return;
				}

				d.hide();

				// Show initializing row in the benches table
				const $tbody = self.$container.find('#benches-table-wrapper tbody');
				if ($tbody.length) {
					$tbody.append(`<tr data-bench="${frappe.utils.escape_html(values.bench_name)}" style="animation: pulse 2s infinite;">
						<td><strong>${frappe.utils.escape_html(values.bench_name)}</strong> <span style="font-size:10px; font-weight:600; background:#dbeafe; color:#1e40af; padding:2px 6px; border-radius:6px; margin-left:6px;">INITIALIZING</span></td>
						<td><span class="bench-path-cell">/home/praveen/${frappe.utils.escape_html(values.bench_name)}</span></td>
						<td><span class="bench-version-cell">${frappe.utils.escape_html(values.frappe_branch)}</span></td>
						<td><span class="badge-status badge-running"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation: spin-animation 1.5s linear infinite; margin-right:4px;"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Initializing...</span></td>
					</tr>`);
				}

				// Start the real backend process
				self.start_pending_op();
				self.show_live_activity(values.bench_name);

				frappe.call({
					method: 'bench_manager.api.init_bench',
					args: {
						bench_name: values.bench_name,
						frappe_branch: values.frappe_branch,
						site_name: values.site_name || '',
						admin_password: values.admin_password || 'admin',
						mariadb_root_password: values.mariadb_root_password || '',
					},
					callback: (r) => {
						if (r.message) {
							frappe.show_alert({
								message: r.message.message,
								indicator: 'blue'
							});
						}
					},
					error: (r) => {
						frappe.show_alert({
							message: 'Failed to start bench initialization',
							indicator: 'red'
						});
						self.stop_pending_op();
					}
				});
			},
		});

		d.show();
		d.$wrapper.find('.modal-dialog').css('max-width', '520px');
	}

	load_benches() {
		const $wrapper = this.$container.find('#benches-table-wrapper');
		$wrapper.html('<div class="loading-placeholder">Discovering benches...</div>');

		frappe.call({
			method: 'bench_manager.api.discover_benches',
			callback: (r) => {
				const benches = r.message || [];
				this.render_benches_table(benches);

				// Update the status bar count
				this.$container.find('#status-benches-count').text(benches.length);

				// Compute total sites and apps across all benches
				let totalSites = 0, totalApps = 0;
				benches.forEach(b => {
					totalSites += (b.sites_count || 0);
					totalApps += (b.apps_count || 0);
				});
				this.$container.find('#status-sites-count').text(totalSites);
				this.$container.find('#status-apps-count').text(totalApps);
			},
			error: () => {
				$wrapper.html(`
					<div class="empty-state">
						<img src="/assets/bench_manager/images/empty_benches.png" alt="Empty" style="max-width: 140px; margin-bottom: 20px;">
						<p>Failed to discover benches. Check server logs.</p>
					</div>`);
			}
		});
	}

	render_benches_table(benches) {
		const self = this;
		const $wrapper = this.$container.find('#benches-table-wrapper');

		if (!benches || !benches.length) {
			$wrapper.html(`
				<div class="empty-state">
					<img src="/assets/bench_manager/images/empty_benches.png" alt="Empty" style="max-width: 140px; margin-bottom: 20px;">
					<p>No benches found. Initialize your first bench!</p>
				</div>`);
			return;
		}

		this._benches_data = benches;

		let html = `<table class="bench-table">
			<thead><tr>
				<th>Bench Name</th><th>Path</th><th>Frappe</th><th>Sites</th><th>Apps</th><th>Status</th><th style="width:80px;text-align:center;">Actions</th>
			</tr></thead><tbody>`;

		benches.forEach((bench) => {
			const isOnline = bench.status === 'Online';
			const badgeClass = isOnline ? 'badge-online' : 'badge-offline';
			const dotClass = isOnline ? 'online' : 'offline';
			const hostTag = bench.is_host ? ' <span class="bench-host-tag">HOST</span>' : '';
			const esc = (s) => frappe.utils.escape_html(s);

			html += `<tr data-bench="${esc(bench.name)}" data-path="${esc(bench.path)}" data-is-host="${bench.is_host ? 1 : 0}">
				<td><strong>${esc(bench.name)}</strong>${hostTag}</td>
				<td><span class="bench-path-cell">${esc(bench.path)}</span></td>
				<td><span class="bench-version-cell">${esc(bench.version)}</span></td>
				<td style="text-align:center;font-weight:600;">${bench.sites_count || 0}</td>
				<td style="text-align:center;font-weight:600;">${bench.apps_count || 0}</td>
				<td><span class="badge-status ${badgeClass}"><span class="status-dot ${dotClass}"></span> ${esc(bench.status)}</span></td>
				<td style="text-align:center;">
					<div class="bench-actions-dropdown">
						<button class="btn btn-xs btn-default bench-actions-toggle" title="Actions" 
							data-bench="${esc(bench.name)}" 
							data-path="${esc(bench.path)}" 
							data-is-host="${bench.is_host ? 1 : 0}"
							data-is-online="${isOnline ? 1 : 0}">
							<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/></svg>
						</button>
					</div>
				</td>
			</tr>`;
		});

		html += '</tbody></table>';
		$wrapper.html(html);
		this._bind_bench_actions();
	}

	_bind_bench_actions() {
		const self = this;

		// Create singleton menu in body if it doesn't exist
		if ($('#bench-actions-singleton').length === 0) {
			$('<div id="bench-actions-singleton" class="bench-actions-menu"></div>').appendTo('body');

			// Bind singleton click events
			$(document).on('click', '#bench-actions-singleton .bench-action-item', function (e) {
				e.preventDefault();
				e.stopPropagation();
				$('#bench-actions-singleton').removeClass('show');
				
				const action = $(this).data('action');
				const benchPath = $(this).data('path');
				const benchName = $(this).data('bench');
				const isHost = $(this).data('is-host') == 1;

				switch (action) {
					case 'view':        self.show_bench_details(benchPath, benchName); break;
					case 'health':      self.show_bench_health(benchPath, benchName); break;
					case 'start':       self.action_start_bench(benchPath, benchName); break;
					case 'stop':        self.action_stop_bench(benchPath, benchName); break;
					case 'open_site':   self.show_open_site_dialog(benchPath, benchName, isHost); break;
					case 'create_site': self.show_create_site_dialog(benchPath, benchName); break;
					case 'backup':      self.action_backup_all(benchPath, benchName); break;
					case 'update':      self.action_update_bench(benchPath, benchName); break;
					case 'sync':        self.show_sync_app_dialog(benchPath, benchName); break;
					case 'delete':      self.action_delete_bench(benchPath, benchName); break;
				}
			});

			// Dismissal
			$(document).on('click.bench-actions', (e) => {
				if (!$(e.target).closest('.bench-actions-toggle').length) {
					$('#bench-actions-singleton').removeClass('show');
				}
			});
			$(window).on('scroll.bench-actions', () => $('#bench-actions-singleton').removeClass('show'));
		}

		// Toggle dropdown — position the singleton menu
		this.$container.find('.bench-actions-toggle').off('click').on('click', function (e) {
			e.stopPropagation();
			const $btn = $(this);
			const benchName = $btn.data('bench');
			const benchPath = $btn.data('path');
			const isHost = $btn.data('is-host') == 1;
			const isOnline = $btn.data('is-online') == 1;

			const $singleton = $('#bench-actions-singleton');
			const wasOpen = $singleton.hasClass('show') && $singleton.data('current-bench') === benchPath;

			// Close if already open for this row
			$singleton.removeClass('show');
			if (wasOpen) return;

			// Build the menu HTML dynamically for this row
			const esc = frappe.utils.escape_html;
			let html = `
				<a class="bench-action-item" data-action="view" data-path="${esc(benchPath)}" data-bench="${esc(benchName)}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg> View Details</a>
				<a class="bench-action-item" data-action="health" data-path="${esc(benchPath)}" data-bench="${esc(benchName)}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg> Health Check</a>
				<div class="bench-action-divider"></div>
				<a class="bench-action-item" data-action="${isOnline ? 'stop' : 'start'}" data-path="${esc(benchPath)}" data-bench="${esc(benchName)}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">${isOnline ? '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>' : '<polygon points="5 3 19 12 5 21 5 3"/>'}</svg> ${isOnline ? 'Stop Bench' : 'Start Bench'}</a>
				<a class="bench-action-item" data-action="open_site" data-path="${esc(benchPath)}" data-bench="${esc(benchName)}" data-is-host="${isHost ? 1 : 0}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg> Open Site</a>
				<a class="bench-action-item" data-action="create_site" data-path="${esc(benchPath)}" data-bench="${esc(benchName)}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/></svg> Create New Site</a>
				<div class="bench-action-divider"></div>
				<a class="bench-action-item" data-action="backup" data-path="${esc(benchPath)}" data-bench="${esc(benchName)}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg> Backup All Sites</a>
				<a class="bench-action-item" data-action="update" data-path="${esc(benchPath)}" data-bench="${esc(benchName)}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> Update Bench</a>
				<a class="bench-action-item" data-action="sync" data-path="${esc(benchPath)}" data-bench="${esc(benchName)}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/></svg> Sync App Here</a>
			`;
			if (!isHost) {
				html += `<div class="bench-action-divider"></div><a class="bench-action-item bench-action-danger" data-action="delete" data-path="${esc(benchPath)}" data-bench="${esc(benchName)}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg> Delete Bench</a>`;
			}

			$singleton.html(html);
			$singleton.data('current-bench', benchPath);

			// Position the menu using fixed coordinates based on the button
			const rect = this.getBoundingClientRect();
			$singleton.css({
				top: rect.bottom + 4 + 'px',
				left: (rect.right - 200) + 'px',  // 200 = min-width of menu, right-align
				zIndex: 99999
			});
			$singleton.addClass('show');
		});
	}

	// ─── P0: View Details ────────────────────────────────────────

	show_bench_details(benchPath, benchName) {
		const d = new frappe.ui.Dialog({
			title: `${benchName} — Sites & Apps`,
			fields: [{ fieldtype: 'HTML', fieldname: 'details_html' }],
			size: 'large',
		});
		d.$wrapper.find('[data-fieldname="details_html"]').html('<div class="text-center p-4 text-muted">Loading...</div>');
		d.show();
		frappe.call({
			method: 'bench_manager.api.get_bench_details',
			args: { bench_path: benchPath },
			callback: (r) => {
				const data = r.message || {};
				const sites = data.sites || [];
				const apps = data.apps || [];
				let html = `<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">`;
				// Sites panel
				html += `<div><h5 style="font-weight:700;margin-bottom:10px;">Sites (${sites.length})</h5>`;
				if (sites.length) {
					html += `<div style="border:1px solid var(--border-color);border-radius:10px;overflow:hidden;">`;
					sites.forEach(s => {
						const stBadge = s.maintenance ? '<span class="badge-status badge-maintenance" style="font-size:10px;margin-left:6px;">Maintenance</span>' : '';
						html += `<div style="padding:10px 14px;border-bottom:1px solid var(--border-color,#f1f5f9);display:flex;align-items:center;gap:8px;">
							<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--primary)" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/></svg>
							<strong style="font-size:13px;">${frappe.utils.escape_html(s.name)}</strong>${stBadge}
							<span style="margin-left:auto;font-size:11px;color:var(--text-muted);font-family:monospace;">${frappe.utils.escape_html(s.db)}</span>
						</div>`;
					});
					html += `</div>`;
				} else { html += `<p class="text-muted">No sites</p>`; }
				html += `</div>`;
				// Apps panel
				html += `<div><h5 style="font-weight:700;margin-bottom:10px;">Apps (${apps.length})</h5>`;
				if (apps.length) {
					html += `<div style="border:1px solid var(--border-color);border-radius:10px;overflow:hidden;">`;
					apps.forEach(a => {
						html += `<div style="padding:10px 14px;border-bottom:1px solid var(--border-color,#f1f5f9);display:flex;align-items:center;gap:8px;">
							<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--primary)" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
							<strong style="font-size:13px;">${frappe.utils.escape_html(a)}</strong>
						</div>`;
					});
					html += `</div>`;
				} else { html += `<p class="text-muted">No apps</p>`; }
				html += `</div></div>`;
				d.$wrapper.find('[data-fieldname="details_html"]').html(html);
			}
		});
	}

	// ─── P0: Start / Stop ────────────────────────────────────────

	action_start_bench(benchPath, benchName) {
		const self = this;
		const d = new frappe.ui.Dialog({
			title: `Start ${benchName}`,
			fields: [{ label: 'Port', fieldname: 'port', fieldtype: 'Int', default: 8001, description: 'Port to start the bench on (avoid 8000 if host is running)' }],
			primary_action_label: 'Start',
			primary_action(v) {
				d.hide();
				frappe.call({
					method: 'bench_manager.api.start_bench',
					args: { bench_path: benchPath, port: v.port },
					callback: (r) => {
						if (r.message.status === 'error') {
							frappe.show_alert({ message: r.message.message, indicator: 'red' });
							return;
						}
						frappe.show_alert({ message: r.message.message, indicator: r.message.status === 'already_running' ? 'orange' : 'green' });
						// Optimistic UI update
						const $row = self.$container.find(`.bench-table tr[data-path="${frappe.utils.escape_html(benchPath)}"]`);
						if ($row.length) {
							$row.find('.badge-status').removeClass('badge-offline').addClass('badge-online').html('<span class="status-dot online"></span> Online');
							$row.find('[data-action="start"]').attr('data-action', 'stop').html('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg> Stop Bench');
						}
						setTimeout(() => self.load_benches(), 2000);
					}
				});
			}
		});
		d.show();
	}

	action_stop_bench(benchPath, benchName) {
		const self = this;
		frappe.confirm(`Stop <strong>${benchName}</strong>? All sites on this bench will go offline.`, () => {
			frappe.call({
				method: 'bench_manager.api.stop_bench',
				args: { bench_path: benchPath },
				callback: (r) => {
					frappe.show_alert({ message: r.message.message, indicator: 'orange' });
					// Optimistic UI update
					const $row = self.$container.find(`.bench-table tr[data-path="${frappe.utils.escape_html(benchPath)}"]`);
					if ($row.length) {
						$row.find('.badge-status').removeClass('badge-online').addClass('badge-offline').html('<span class="status-dot offline"></span> Offline');
						$row.find('[data-action="stop"]').attr('data-action', 'start').html('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg> Start Bench');
					}
					setTimeout(() => self.load_benches(), 2000);
				}
			});
		});
	}

	// ─── P1: Backup All Sites ────────────────────────────────────

	action_backup_all(benchPath, benchName) {
		const self = this;
		frappe.confirm(`Backup all sites on <strong>${benchName}</strong>? This runs in the background.`, () => {
			self.start_pending_op();
			self.show_live_activity(benchName);
			frappe.call({
				method: 'bench_manager.api.backup_all_bench_sites',
				args: { bench_path: benchPath },
				callback: (r) => frappe.show_alert({ message: r.message.message, indicator: 'blue' })
			});
		});
	}

	// ─── P1: Delete Bench ────────────────────────────────────────

	action_delete_bench(benchPath, benchName) {
		const self = this;
		frappe.confirm(`<div style="text-align:center;">
			<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2" style="margin-bottom:8px;"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
			<p style="font-weight:700;font-size:15px;margin-bottom:4px;">Delete ${benchName}?</p>
			<p style="color:var(--text-muted);font-size:13px;">This will permanently remove the entire bench directory including all sites, databases, and apps. This cannot be undone.</p>
		</div>`, () => {
			frappe.call({
				method: 'bench_manager.api.delete_bench',
				args: { bench_path: benchPath, confirm: 'yes' },
				callback: (r) => {
					frappe.show_alert({ message: r.message.message, indicator: 'green' });
					self.load_benches();
					self.populate_context_selector();
				},
				error: () => frappe.show_alert({ message: 'Delete failed — check server logs.', indicator: 'red' })
			});
		});
	}

	// ─── P2: Update Bench ────────────────────────────────────────

	action_update_bench(benchPath, benchName) {
		const self = this;
		frappe.confirm(`Run <strong>bench update</strong> on <strong>${benchName}</strong>? This pulls latest code and runs migrations.`, () => {
			self.start_pending_op();
			self.show_live_activity(benchName);
			frappe.call({
				method: 'bench_manager.api.update_remote_bench',
				args: { bench_path: benchPath },
				callback: (r) => frappe.show_alert({ message: r.message.message, indicator: 'blue' })
			});
		});
	}

	// ─── P2: Health Check ────────────────────────────────────────

	show_bench_health(benchPath, benchName) {
		const d = new frappe.ui.Dialog({
			title: `Health Check — ${benchName}`,
			fields: [{ fieldtype: 'HTML', fieldname: 'health_html' }],
		});
		d.$wrapper.find('[data-fieldname="health_html"]').html('<div class="text-center p-4 text-muted">Checking...</div>');
		d.show();
		frappe.call({
			method: 'bench_manager.api.bench_health_check',
			args: { bench_path: benchPath },
			callback: (r) => {
				const h = r.message;
				const ok = (v) => `<span style="color:#10b981;font-weight:600;">✓ ${v}</span>`;
				const warn = (v) => `<span style="color:#f59e0b;font-weight:600;">⚠ ${v}</span>`;
				const fail = (v) => `<span style="color:#ef4444;font-weight:600;">✕ ${v}</span>`;
				let html = `<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">`;
				const card = (icon, label, value) => `<div style="padding:14px;border:1px solid var(--border-color);border-radius:10px;display:flex;align-items:center;gap:12px;">${icon}<div><div style="font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--text-muted);font-weight:700;">${label}</div><div style="font-size:15px;font-weight:700;color:var(--text-color);">${value}</div></div></div>`;
				html += card('💾', 'Disk Usage', h.disk_usage);
				html += card('🐍', 'Python', h.python_version);
				html += card('📦', 'Node.js', h.node_version);
				html += card('🌐', 'Sites', h.sites_count);
				html += card('🧩', 'Apps', h.apps_count);
				html += card('⚙️', 'Virtualenv', h.venv_valid ? ok('Valid') : fail('Broken'));
				html += card('📄', 'Procfile', h.procfile ? ok('Present') : warn('Missing'));
				html += `</div>`;
				d.$wrapper.find('[data-fieldname="health_html"]').html(html);
			}
		});
	}

	// ─── P3: Sync App ────────────────────────────────────────────

	show_sync_app_dialog(targetPath, targetName) {
		const self = this;
		// Step 1: Get apps from host bench
		frappe.call({
			method: 'bench_manager.api.list_apps',
			callback: (r) => {
				const apps = (r.message || []).map(a => a.app_name).filter(a => a !== 'frappe');
				if (!apps.length) { frappe.msgprint('No custom apps found on host bench.'); return; }

				// Step 2: Get target bench details for site list
				frappe.call({
					method: 'bench_manager.api.get_bench_details',
					args: { bench_path: targetPath },
					callback: (r2) => {
						const sites = (r2.message?.sites || []).map(s => s.name);
						const siteOptions = sites.length ? ['', ...sites].join('\n') : '';

						const d = new frappe.ui.Dialog({
							title: `Sync App to ${targetName}`,
							fields: [
								{
									fieldtype: 'HTML', fieldname: 'compat_html',
									options: `<div style="padding:8px 0; color:var(--text-muted); font-size:13px;">Select an app to check version compatibility and sync.</div>`
								},
								{
									label: 'App to Sync', fieldname: 'app_name', fieldtype: 'Select',
									options: apps.join('\n'), reqd: 1,
									description: `This app will be copied from the host bench to <strong>${frappe.utils.escape_html(targetName)}</strong>`,
									onchange: () => {
										const appName = d.get_value('app_name');
										if (!appName) return;
										// Check compatibility
										frappe.call({
											method: 'bench_manager.api.get_app_compatibility',
											args: { app_name: appName, target_bench_path: targetPath },
											callback: (cr) => {
												const c = cr.message;
												let html;
												if (c.compatible) {
													html = `<div style="padding:10px 14px; background:#f0fdf4; border:1px solid #bbf7d0; border-radius:10px; margin:8px 0;">
														<div style="font-weight:700; color:#15803d; margin-bottom:4px;">✓ Version Compatible</div>
														<div style="font-size:12px; color:#166534;">Host: v${c.host_version} → Target: v${c.target_version}</div>
													</div>`;
												} else {
													html = `<div style="padding:10px 14px; background:#fef3c7; border:1px solid #fde68a; border-radius:10px; margin:8px 0;">
														<div style="font-weight:700; color:#92400e; margin-bottom:4px;">⚠ Version Mismatch</div>
														<div style="font-size:12px; color:#92400e;">Host: v${c.host_version} (v${c.host_major}) → Target: v${c.target_version} (v${c.target_major})</div>
														<div style="font-size:11px; color:#92400e; margin-top:4px;">The app may not be compatible. Consider updating the target bench to match the host version first.</div>
													</div>`;
												}
												d.fields_dict.compat_html.$wrapper.html(html);
											}
										});
									}
								},
								{ fieldtype: 'Section Break', label: 'Install on Site (Optional)' },
								{
									label: 'Install on Site', fieldname: 'install_site', fieldtype: 'Select',
									options: siteOptions,
									description: sites.length ? 'Select a site to install the app on after syncing (optional)' : 'No sites found on this bench',
								},
							],
							primary_action_label: 'Sync App',
							primary_action(v) {
								d.hide();
								self.start_pending_op();
								self.show_live_activity(targetName);
								// Step 3: Sync the app
								frappe.call({
									method: 'bench_manager.api.sync_app_to_bench',
									args: { app_name: v.app_name, target_bench_path: targetPath },
									callback: (r) => {
										frappe.show_alert({ message: r.message.message, indicator: 'blue' });
										// Step 4: If a site was selected, install the app on it after a delay
										if (v.install_site) {
											setTimeout(() => {
												frappe.call({
													method: 'bench_manager.api.install_app_on_site_remote',
													args: {
														bench_path: targetPath,
														site_name: v.install_site,
														app_name: v.app_name,
													},
													callback: (ir) => frappe.show_alert({ message: ir.message.message, indicator: 'blue' }),
												});
											}, 5000); // Wait for get-app to complete
										}
									}
								});
							}
						});
						d.show();
						d.$wrapper.find('.modal-dialog').css('max-width', '520px');
					}
				});
			}
		});
	}

	// ─── Open Site in Browser ─────────────────────────────────────

	show_open_site_dialog(benchPath, benchName, isHost) {
		const self = this;
		frappe.call({
			method: 'bench_manager.api.get_bench_details',
			args: { bench_path: benchPath },
			callback: (r) => {
				const sites = (r.message?.sites || []).map(s => s.name);
				if (!sites.length) {
					frappe.msgprint(`No sites found on <strong>${frappe.utils.escape_html(benchName)}</strong>. Create a site first.`);
					return;
				}
				frappe.call({
					method: 'bench_manager.api.get_bench_port',
					args: { bench_path: benchPath },
					callback: (r2) => {
						// For host bench, always use current window port (it's running)
						let port = r2.message?.port || 8000;
						if (isHost && window.location.port) {
							port = window.location.port;
						}
						const d = new frappe.ui.Dialog({
							title: `Open Site on ${benchName}`,
							fields: [
								{
									fieldtype: 'HTML',
									options: `<div style="padding:8px 0 10px; margin-bottom:8px; border-bottom:1px solid var(--border-color);">
										<p style="margin:0; color:var(--text-muted); font-size:13px; line-height:1.6;">
											Select a site to open. It will be set as the <strong>default site</strong> for this bench 
											and opened in your browser on port <code>${port}</code>.
										</p>
									</div>`
								},
								{
									label: 'Select Site', fieldname: 'site_name', fieldtype: 'Select',
									options: sites.join('\n'), reqd: 1,
								},
								{
									fieldtype: 'HTML', fieldname: 'site_url_preview',
									options: `<div style="padding:10px 14px; background:var(--bg-light-gray); border-radius:10px; margin-top:6px;">
										<div style="font-size:11px; text-transform:uppercase; letter-spacing:.5px; color:var(--text-muted); font-weight:700; margin-bottom:4px;">URL Preview</div>
										<div style="font-size:14px; font-weight:600; color:var(--primary);">http://localhost:${port}</div>
										<div style="font-size:11px; color:var(--text-muted); margin-top:4px;">Site: ${frappe.utils.escape_html(sites[0])}</div>
									</div>`
								},
							],
							primary_action_label: 'Set Default & Open',
							primary_action(values) {
								d.hide();
								frappe.show_alert({ message: `Setting ${values.site_name} as default...`, indicator: 'blue' });
								frappe.call({
									method: 'bench_manager.api.set_default_site_on_bench',
									args: { bench_path: benchPath, site_name: values.site_name },
									callback: (r) => {
										frappe.show_alert({ message: r.message.message, indicator: 'green' });
										const url = `http://localhost:${port}`;
										window.open(url, '_blank');
									},
									error: () => {
										frappe.show_alert({ message: 'Failed to set default site', indicator: 'red' });
									}
								});
							}
						});

						d.fields_dict.site_name.$input.on('change', function () {
							const site = $(this).val();
							d.fields_dict.site_url_preview.$wrapper.html(
								`<div style="padding:10px 14px; background:var(--bg-light-gray); border-radius:10px; margin-top:6px;">
									<div style="font-size:11px; text-transform:uppercase; letter-spacing:.5px; color:var(--text-muted); font-weight:700; margin-bottom:4px;">URL Preview</div>
									<div style="font-size:14px; font-weight:600; color:var(--primary);">http://localhost:${port}</div>
									<div style="font-size:11px; color:var(--text-muted); margin-top:4px;">Site: ${frappe.utils.escape_html(site)}</div>
								</div>`
							);
						});

						d.show();
					}
				});
			}
		});
	}


	// ─── Create Site on Any Bench ────────────────────────────────

	show_create_site_dialog(benchPath, benchName) {
		const self = this;
		const d = new frappe.ui.Dialog({
			title: `Create New Site on ${benchName}`,
			fields: [
				{
					fieldtype: 'HTML',
					options: `<div style="padding:10px 0 8px; margin-bottom:8px; border-bottom:1px solid var(--border-color);">
						<p style="margin:0; color:var(--text-muted); font-size:13px; line-height:1.6;">
							This creates a new Frappe site on <strong>${frappe.utils.escape_html(benchName)}</strong>.
							A MariaDB database will be created and Frappe will be installed.
						</p>
					</div>`
				},
				{
					label: 'Site Name',
					fieldname: 'site_name',
					fieldtype: 'Data',
					reqd: 1,
					placeholder: 'e.g., mysite.localhost',
					description: 'Must contain a dot (e.g., myapp.localhost)',
				},
				{
					label: 'Admin Password',
					fieldname: 'admin_password',
					fieldtype: 'Password',
					default: 'admin',
					reqd: 1,
					description: 'Password for the Administrator user',
				},
				{
					label: 'MariaDB Root Password',
					fieldname: 'mariadb_root_password',
					fieldtype: 'Password',
					description: 'Required if your MariaDB root user has a password',
				},
			],
			size: 'small',
			primary_action_label: 'Create Site',
			primary_action(values) {
				if (!values.site_name || !values.site_name.includes('.')) {
					frappe.msgprint('Site name must contain a dot (e.g., mysite.localhost)');
					return;
				}
				d.hide();
				self.start_pending_op();
				self.show_live_activity(benchName);
				frappe.call({
					method: 'bench_manager.api.create_site_on_bench',
					args: {
						bench_path: benchPath,
						site_name: values.site_name,
						admin_password: values.admin_password || 'admin',
						mariadb_root_password: values.mariadb_root_password || '',
					},
					callback: (r) => {
						frappe.show_alert({ message: r.message.message, indicator: 'blue' });
					},
					error: () => {
						frappe.show_alert({ message: 'Failed to start site creation', indicator: 'red' });
						self.stop_pending_op();
					}
				});
			}
		});
		d.show();
	}

	// ─── Sites Tab ───────────────────────────────────────────────

	load_sites() {
		const $wrapper = this.$container.find('#sites-table-wrapper');
		$wrapper.html('<div class="loading-placeholder">Loading sites...</div>');

		const method = this.is_host_bench ? 'bench_manager.api.list_sites' : 'bench_manager.api.list_bench_sites';
		const args = this.is_host_bench ? {} : { bench_path: this.current_bench_path };

		frappe.call({
			method: method,
			args: args,
			callback: (r) => {
				if (r.message && r.message.length) {
					this.render_sites_table(r.message);
				} else {
					$wrapper.html(`
						<div class="empty-state">
							<img src="/assets/bench_manager/images/empty_sites.png" alt="Empty" style="max-width: 140px; margin-bottom: 20px;">
							<p>No sites found. Create your first site!</p>
						</div>
					`);
				}
			},
		});
	}

	render_sites_table(sites) {
		const $wrapper = this.$container.find('#sites-table-wrapper');
		let html = `<table class="bench-table">
			<thead><tr>
				<th>Site Name</th><th>Status</th><th>Apps</th><th style="width: 50px;"></th>
			</tr></thead><tbody>`;

		sites.forEach((site) => {
			const badgeClass = `badge-${(site.status || 'unknown').toLowerCase()}`;
			html += `<tr data-site="${frappe.utils.escape_html(site.site_name)}">
				<td><strong>${frappe.utils.escape_html(site.site_name)}</strong></td>
				<td><span class="badge-status ${badgeClass}">${frappe.utils.escape_html(site.status)}</span></td>
				<td>
					<button class="btn btn-xs btn-default show-apps-btn" data-site="${frappe.utils.escape_html(site.site_name)}" style="padding: 3px 12px; border-radius: 6px; font-size: 12px; font-weight: 500; border: 1px solid #d0d5dd; color: #344054; background: #fff; cursor: pointer; transition: all 0.2s ease;">
						<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 4px; vertical-align: -2px;"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
						Show Apps
					</button>
				</td>
				<td style="text-align: right;">
					<div class="dropdown">
						<button class="btn btn-default btn-xs" data-toggle="dropdown" style="padding: 4px; box-shadow: none;">
							<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="1"/><circle cx="12" cy="5" r="1"/><circle cx="12" cy="19" r="1"/></svg>
						</button>
						<ul class="dropdown-menu dropdown-menu-right" style="min-width: 180px; padding: 6px 0; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);">
							<li><a class="dropdown-item site-start" href="#" data-site="${frappe.utils.escape_html(site.site_name)}" style="padding: 8px 16px; display: block;">▶ Start</a></li>
							<li><a class="dropdown-item site-stop" href="#" data-site="${frappe.utils.escape_html(site.site_name)}" style="padding: 8px 16px; display: block;">■ Stop</a></li>
							<li><a class="dropdown-item site-open ${site.status !== 'Active' ? 'text-muted' : ''}" href="#" data-site="${frappe.utils.escape_html(site.site_name)}" data-status="${site.status}" style="padding: 8px 16px; display: block; ${site.status !== 'Active' ? 'opacity: 0.6; cursor: not-allowed;' : ''}">↗ Open Site</a></li>
							<li><a class="dropdown-item site-live-activity" href="#" data-site="${frappe.utils.escape_html(site.site_name)}" style="padding: 8px 16px; display: block;">⚡ Live Activity</a></li>
							<li class="divider" style="margin: 4px 0;"></li>
							<li><a class="dropdown-item site-migrate" href="#" data-site="${frappe.utils.escape_html(site.site_name)}" style="padding: 8px 16px; display: block;">Migrate</a></li>
							<li><a class="dropdown-item site-backup" href="#" data-site="${frappe.utils.escape_html(site.site_name)}" style="padding: 8px 16px; display: block;">Backup</a></li>
							<li><a class="dropdown-item site-maintenance" href="#" data-site="${frappe.utils.escape_html(site.site_name)}" data-mode="${site.status === 'Maintenance' ? '0' : '1'}" style="padding: 8px 16px; display: block;">${site.status === 'Maintenance' ? 'Disable Maint.' : 'Maintenance'}</a></li>
							<li class="divider" style="margin: 4px 0;"></li>
							<li><a class="dropdown-item site-drop text-danger" href="#" data-site="${frappe.utils.escape_html(site.site_name)}" style="padding: 8px 16px; color: #dc3545; display: block;">✕ Drop Site</a></li>
						</ul>
					</div>
				</td>
			</tr>`;
		});

		html += '</tbody></table>';
		$wrapper.html(html);
		this.bind_site_row_actions();
	}

	bind_site_row_actions() {
		const self = this;

		// Show Apps dialog
		this.$container.find('.show-apps-btn').on('click', function (e) {
			e.preventDefault();
			const site = $(this).data('site');
			const $btn = $(this);
			const originalHtml = $btn.html();
			$btn.html('<span style="font-size: 12px;">Loading...</span>').prop('disabled', true);

			frappe.call({
				method: 'bench_manager.api.get_site_apps',
				args: { site_name: site },
				callback: (r) => {
					$btn.html(originalHtml).prop('disabled', false);
					const apps = r.message || [];
					let body = '';
					if (apps.length) {
						body = `<div style="padding: 4px 0;">
							<div style="display: flex; padding: 6px 12px; margin-bottom: 4px; font-size: 11px; color: #667085; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;">
								<span style="flex: 2;">App</span>
								<span style="flex: 1; text-align: center;">Version</span>
								<span style="flex: 1; text-align: right;">Branch</span>
							</div>`;
						apps.forEach((a, i) => {
							const bg = i % 2 === 0 ? '#f8fafc' : '#fff';
							body += `<div style="display: flex; align-items: center; padding: 10px 12px; margin: 2px 0; background: ${bg}; border-radius: 6px; font-size: 13px;">
								<span style="flex: 2; font-weight: 600; color: #1d2939;">
									<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#2490ef" stroke-width="2" style="margin-right: 6px; vertical-align: -2px;"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
									${frappe.utils.escape_html(a.app_name)}
								</span>
								<span style="flex: 1; text-align: center; color: #667085; font-size: 12px;">${frappe.utils.escape_html(a.version || '—')}</span>
								<span style="flex: 1; text-align: right;"><span style="background: #ecfdf3; color: #027a48; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 500;">${frappe.utils.escape_html(a.branch || '—')}</span></span>
							</div>`;
						});
						body += `<div style="margin-top: 10px; padding: 8px 12px; text-align: right; font-size: 12px; color: #667085;">${apps.length} app${apps.length > 1 ? 's' : ''} installed</div></div>`;
					} else {
						body = `<div style="padding: 30px; text-align: center;">
							<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#d0d5dd" stroke-width="1.5" style="margin-bottom: 10px;"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
							<p class="text-muted" style="margin: 0;">No apps installed on this site.</p>
						</div>`;
					}
					const d = new frappe.ui.Dialog({
						title: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 6px; vertical-align: -2px;"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg> Apps on ${frappe.utils.escape_html(site)}`,
						fields: [{ fieldtype: 'HTML', options: body }]
					});
					d.show();
					d.$wrapper.find('.modal-dialog').css('max-width', '520px');
				},
				error: () => {
					$btn.html(originalHtml).prop('disabled', false);
				}
			});
		});

		this.$container.find('.site-start').on('click', function (e) {
			e.preventDefault();
			const site = $(this).data('site');
			frappe.call({
				method: 'bench_manager.api.get_current_site',
				callback: (r) => {
					const cs = r.message;
					if (cs && cs !== site) {
						frappe.msgprint(`Site <strong>${cs}</strong> is currently active. Stop it first before starting <strong>${site}</strong>.`);
					} else if (cs === site) {
						frappe.show_alert({ message: `${site} is already active.`, indicator: 'blue' });
					} else {
						self.append_console(`Setting ${site} as current site...`, 'command');
						frappe.call({
							method: 'bench_manager.api.set_current_site',
							args: { site_name: site },
							callback: () => {
								self.append_console(`${site} is now the active site.`, 'success');
								setTimeout(() => self.load_sites(), 1500);
							}
						});
					}
				}
			});
		});

		this.$container.find('.site-stop').on('click', function (e) {
			e.preventDefault();
			const site = $(this).data('site');
			frappe.call({
				method: 'bench_manager.api.get_current_site',
				callback: (r) => {
					if (r.message !== site) {
						frappe.show_alert({ message: `${site} is not currently active.`, indicator: 'orange' });
					} else {
						self.append_console(`Stopping site ${site}...`, 'command');
						frappe.call({
							method: 'bench_manager.api.clear_current_site',
							args: { site_name: site },
							callback: () => {
								self.append_console(`${site} stopped. No active site now.`, 'success');
								setTimeout(() => self.load_sites(), 1000);
							}
						});
					}
				}
			});
		});

		this.$container.find('.site-open').on('click', function (e) {
			e.preventDefault();
			const site = $(this).data('site');
			const status = $(this).data('status');
			
			if (status !== 'Active') {
				frappe.msgprint('Only the active site can be opened. Please start this site first.');
				return;
			}
			
			frappe.show_alert({ message: `Setting ${site} as default and opening...`, indicator: 'blue' });
			// Set as default site then open via localhost (uses current window's port = host bench)
			frappe.call({
				method: 'bench_manager.api.set_current_site',
				args: { site_name: site },
				callback: () => {
					// Small delay so Frappe picks up config change
					setTimeout(() => {
						const port = window.location.port ? `:${window.location.port}` : '';
						window.open(`${window.location.protocol}//localhost${port}`, '_blank');
					}, 300);
				},
				error: () => {
					frappe.show_alert({ message: 'Failed to set site as default', indicator: 'red' });
				}
			});
		});

		this.$container.find('.site-live-activity').on('click', function (e) {
			e.preventDefault();
			self.show_live_activity($(this).data('site'));
		});

		this.$container.find('.site-migrate').on('click', function (e) {
			e.preventDefault();
			const site = $(this).data('site');
			const $row = $(this).closest('tr');
			frappe.confirm(`Migrate site <strong>${site}</strong>?`, () => {
				$row.find('.badge-status').removeClass().addClass('badge-status badge-warning').text('Migrating...');
				self.append_console(`$ bench --site ${site} migrate`, 'command');
				self.append_console(`Starting migration for site ${site}...`, 'stdout');
				self.show_live_activity(site);
				frappe.call({
					method: 'bench_manager.api.migrate_site',
					args: { site_name: site },
					callback: (r) => {
						if (r.message) frappe.show_alert({ message: r.message.message, indicator: 'blue' });
					},
				});
			});
		});

		this.$container.find('.site-backup').on('click', function (e) {
			e.preventDefault();
			const site = $(this).data('site');
			const $row = $(this).closest('tr');
			
			const d = new frappe.ui.Dialog({
				title: `Backup Site: ${site}`,
				fields: [
					{
						fieldname: 'with_files',
						fieldtype: 'Check',
						label: 'Backup With Files',
						default: 1
					}
				],
				primary_action_label: 'Start Backup',
				primary_action: (values) => {
					d.hide();
					$row.find('.badge-status').removeClass().addClass('badge-status badge-warning').text('Backing Up...');
					
					let cmdText = `$ bench --site ${site} backup`;
					if (values.with_files) cmdText += ' --with-files';
					
					self.append_console(cmdText, 'command');
					self.append_console(`Starting backup for site ${site}...`, 'stdout');
					self.show_live_activity(site);
					
					frappe.call({
						method: 'bench_manager.api.backup_site',
						args: { 
							site_name: site,
							with_files: values.with_files ? 1 : 0
						},
						callback: (r) => {
							if (r.message) frappe.show_alert({ message: r.message.message, indicator: 'blue' });
						},
					});
				}
			});
			d.show();
		});

		this.$container.find('.site-maintenance').on('click', function (e) {
			e.preventDefault();
			const site = $(this).data('site');
			const mode = $(this).data('mode');
			const action = mode === 1 ? 'enable' : 'disable';
			frappe.confirm(`${action.charAt(0).toUpperCase() + action.slice(1)} maintenance mode for <strong>${site}</strong>?`, () => {
				frappe.call({
					method: 'bench_manager.api.toggle_maintenance_mode',
					args: { site_name: site, enable: mode },
					callback: (r) => {
						if (r.message) {
							frappe.show_alert({ message: r.message.message, indicator: 'green' });
							self.load_sites();
						}
					},
				});
			});
		});

		this.$container.find('.site-drop').on('click', function (e) {
			e.preventDefault();
			const site = $(this).data('site');
			const $row = $(this).closest('tr');
			const d = new frappe.ui.Dialog({
				title: `Drop Site: ${site}`,
				fields: [
					{ fieldtype: 'HTML', options: `<p style="color: #dc3545; font-weight: 600;">⚠ This will permanently delete site <strong>${site}</strong> and all its data.</p>` },
					{ label: 'Database Root Password', fieldname: 'db_root_password', fieldtype: 'Password', description: 'Required to drop the database.' },
				],
				primary_action_label: 'Drop Site',
				primary_action(values) {
					d.hide();
					$row.find('.badge-status').removeClass().addClass('badge-status badge-dropping').text('Dropping...');
					$row.css('opacity', '0.5');
					self.append_console(`$ bench drop-site ${site} --force --no-backup`, 'command');
					self.append_console(`Initiating site deletion for ${site}. Removing database and files...`, 'stdout');
					self.show_live_activity(site);
					frappe.call({
						method: 'bench_manager.api.drop_site',
						args: { site_name: site, db_root_password: values.db_root_password || null },
						callback: (r) => {
							if (r.message) {
								frappe.show_alert({ message: r.message.message, indicator: 'orange' });
							}
						},
					});
				},
			});
			d.show();
		});
	}

	setup_site_actions() {
		this.$container.find('#btn-new-site').on('click', () => this.show_new_site_dialog());
		this.$container.find('#btn-refresh-sites').on('click', () => {
			this.load_sites();
			this.load_status();
		});
	}

	show_live_activity(site) {
		const self = this;
		this.start_pending_op();
		
		this.live_activity_dialog = new frappe.ui.Dialog({
			title: `Live Activity <span style="color: #2490ef; font-size: 13px; font-weight: normal; margin-left: 10px;">Running <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation: spin-animation 2s linear infinite;"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg></span>`,
			fields: [
				{
					fieldtype: 'HTML',
					fieldname: 'terminal',
					options: `
						<style>
							@keyframes spin-animation { 100% { transform: rotate(360deg); } }
						</style>
						<div class="terminal-wrapper" style="margin-top: -10px; position: relative;">
							<p class="text-muted" style="margin-bottom: 12px; font-size: 13px;">Verbose logs from background orchestration tasks for <strong>${site}</strong>.</p>
							<div class="terminal-container" style="background: var(--bg-primary, #1e1e1e); color: var(--text-color, #d4d4d4); padding: 15px; border-radius: 8px; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px; height: 350px; overflow-y: auto; position: relative; z-index: 1;">
								<img src="/assets/bench_manager/images/empty_benches.png" style="position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); max-width: 200px; opacity: 0.15; pointer-events: none; z-index: 0;">
								<div id="live-activity-output" style="position: relative; z-index: 2;"></div>
							</div>
						</div>
					`
				}
			],
			primary_action_label: 'Close',
			primary_action() {
				self.live_activity_dialog.hide();
			}
		});

		// Add custom copy button alongside the close button
		const $footer = this.live_activity_dialog.$wrapper.find('.modal-footer');
		$footer.css('justify-content', 'space-between');
		$footer.prepend(`
			<button class="btn btn-default btn-sm" id="btn-copy-logs">
				<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 4px; vertical-align: text-bottom;"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
				Copy
			</button>
		`);

		this.live_activity_dialog.$wrapper.find('#btn-copy-logs').on('click', () => {
			const text = this.console_logs.map(l => `[${l.time}] ${l.message}`).join('\\n');
			frappe.utils.copy_to_clipboard(text);
			frappe.show_alert({ message: 'Logs copied to clipboard', indicator: 'green' });
		});

		this.live_activity_dialog.show();

		// Start SSE polling for zero-latency live logs
		this._start_sse_polling();

		// Stop SSE polling when dialog is closed
		this.live_activity_dialog.$wrapper.on('hidden.bs.modal', () => {
			this._stop_sse_polling();
		});

		// Set width to make it look like the reference image
		this.live_activity_dialog.$wrapper.find('.modal-dialog').css({
			'max-width': '800px',
			'width': '90%'
		});

		// Populate existing logs
		const $console = this.live_activity_dialog.$wrapper.find('#live-activity-output');
		this.console_logs.forEach(log => {
			const cssClass = `console-${log.type || 'stdout'}`;
			const $line = $(`<div class="console-line ${cssClass}"><span class="log-time" style="color: #858585; margin-right: 15px;">${log.time}</span><span class="log-msg"></span></div>`);
			$line.find('.log-msg').text(log.message);
			$console.append($line);
		});

		// Scroll to bottom
		setTimeout(() => {
			const terminal = this.live_activity_dialog.$wrapper.find('.terminal-container')[0];
			if (terminal) terminal.scrollTop = terminal.scrollHeight;
		}, 100);
	}

	show_new_site_dialog() {
		const self = this;
		const bench_options = self.benches ? self.benches.map(b => ({ label: b.is_host ? `${b.name} (host)` : b.name, value: b.path })) : [];

		const d = new frappe.ui.Dialog({
			title: 'Create New Site',
			fields: [
				{ label: 'Target Bench', fieldname: 'bench_path', fieldtype: 'Select', options: bench_options, default: self.current_bench_path, reqd: 1 },
				{ label: 'Site Name', fieldname: 'site_name', fieldtype: 'Data', reqd: 1, description: 'e.g., mysite.localhost' },
				{ label: 'Admin Password', fieldname: 'admin_password', fieldtype: 'Password', reqd: 1 },
				{ label: 'Database Root Password', fieldname: 'db_password', fieldtype: 'Password', description: 'Leave blank if not required' },
			],
			primary_action_label: 'Create Site',
			primary_action(values) {
				d.hide();
				
				const is_host = values.bench_path === (self.benches.find(b => b.is_host) || {}).path;
				const bench_name = (self.benches.find(b => b.path === values.bench_path) || {}).name || 'host';

				// Prepend row with live activity link
				const new_row = `<tr data-site="${frappe.utils.escape_html(values.site_name)}">
					<td><strong>${frappe.utils.escape_html(values.site_name)}</strong></td>
					<td><span class="badge-status badge-creating">Creating...</span></td>
					<td><a href="#" class="show-creating-activity" data-site="${frappe.utils.escape_html(values.site_name)}" style="color: #2490ef; font-size: 12px;">⚡ View Live Activity</a></td>
					<td></td>
				</tr>`;
				self.$container.find('#sites-table-wrapper tbody').prepend(new_row);
				
				// Bind the live activity link on the new row
				self.$container.find('.show-creating-activity').on('click', function(e) {
					e.preventDefault();
					self.show_live_activity($(this).data('site'));
				});
				
				self.append_console(`$ bench new-site ${values.site_name} --admin-password ***`, 'command');
				self.append_console(`Queuing site creation for ${values.site_name} on ${bench_name}...`, 'stdout');
				self.append_console(`This will create the database, install frappe, and set up the site.`, 'info');
				self.show_live_activity(values.site_name);
				
				const method = is_host ? 'bench_manager.api.create_site' : 'bench_manager.api.create_site_on_bench';
				const args = Object.assign({}, values);
				if (!is_host) args.bench_path = values.bench_path;

				frappe.call({
					method: method,
					args: args,
					callback: (r) => {
						if (r.message) {
							frappe.show_alert({ message: r.message.message, indicator: 'blue' });
						}
					},
				});
			},
		});
		d.show();
	}

	// ─── Apps Tab ────────────────────────────────────────────────

	load_apps() {
		const $wrapper = this.$container.find('#apps-table-wrapper');
		$wrapper.html('<div class="loading-placeholder">Loading apps...</div>');

		const method = this.is_host_bench ? 'bench_manager.api.list_apps' : 'bench_manager.api.list_bench_apps';
		const args = this.is_host_bench ? {} : { bench_path: this.current_bench_path };

		frappe.call({
			method: method,
			args: args,
			callback: (r) => {
				if (r.message && r.message.length) {
					this.render_apps_table(r.message);
				} else {
					$wrapper.html(`
						<div class="empty-state">
							<img src="/assets/bench_manager/images/empty_sites.png" alt="Empty" style="max-width: 140px; margin-bottom: 20px;">
							<p>No apps found.</p>
						</div>
					`);
				}
			},
		});
	}

	render_apps_table(apps) {
		const $wrapper = this.$container.find('#apps-table-wrapper');

		// Also load sites for the install dropdown
		const site_method = this.is_host_bench ? 'bench_manager.api.list_sites' : 'bench_manager.api.list_bench_sites';
		const site_args = this.is_host_bench ? {} : { bench_path: this.current_bench_path };

		frappe.call({
			method: site_method,
			args: site_args,
			callback: (r) => {
				const sites = (r.message || []).map((s) => s.site_name);
				let html = `<table class="bench-table">
					<thead><tr>
						<th>App Name</th><th>Git URL</th><th>Branch</th><th>Actions</th>
					</tr></thead><tbody>`;

				apps.forEach((app) => {
					// Determine source category
					let source = 'custom';
					let badgeHtml = '<span class="badge-source badge-source-custom">Custom</span>';
					
					const official_apps = ['frappe', 'erpnext', 'hrms', 'lms', 'builder', 'helpdesk', 'gamecenter', 'wiki', 'insights', 'crm', 'print_designer', 'desk', 'payments', 'ecommerce_integrations'];
					
					if (official_apps.includes(app.app_name) || (app.git_url && app.git_url.includes('github.com/frappe/'))) {
						source = 'frappe_store';
						badgeHtml = '<span class="badge-source badge-source-frappe">Frappe</span>';
					} else if (app.git_url) {
						source = 'git';
						badgeHtml = '<span class="badge-source badge-source-git">Git</span>';
					}

					const gitUrl = app.git_url ? `<a href="${frappe.utils.escape_html(app.git_url)}" target="_blank">${frappe.utils.escape_html(app.git_url)}</a>` : '—';
					html += `<tr data-source="${source}">
						<td>
							<div style="display:flex;flex-direction:column;gap:3px;">
								<strong>${frappe.utils.escape_html(app.app_name)}</strong>
								${badgeHtml}
							</div>
						</td>
						<td><small>${gitUrl}</small></td>
						<td>${frappe.utils.escape_html(app.branch || '—')}</td>
						<td>
							<div class="dropdown">
								<button class="btn btn-default btn-xs" data-toggle="dropdown" style="padding: 4px; box-shadow: none;">
									<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="1"/><circle cx="12" cy="5" r="1"/><circle cx="12" cy="19" r="1"/></svg>
								</button>
								<ul class="dropdown-menu dropdown-menu-right" style="min-width: 160px; padding: 4px 0; border-radius: 6px; box-shadow: 0 4px 12px rgba(0,0,0,0.15);">
									<li><a href="#" class="app-install" data-app="${frappe.utils.escape_html(app.app_name)}" style="padding: 6px 12px; display: block; color: var(--text-color);"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 6px; vertical-align: -2px;"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> Install</a></li>
									<li><a href="#" class="app-uninstall" data-app="${frappe.utils.escape_html(app.app_name)}" style="padding: 6px 12px; display: block; color: var(--text-color);"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 6px; vertical-align: -2px;"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg> Uninstall</a></li>
									<li role="separator" class="divider" style="margin: 4px 0;"></li>
									<li><a href="#" class="app-sites" data-app="${frappe.utils.escape_html(app.app_name)}" style="padding: 6px 12px; display: block; color: var(--text-color);"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 6px; vertical-align: -2px;"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg> Sites</a></li>
									<li><a href="#" class="app-live-activity" data-app="${frappe.utils.escape_html(app.app_name)}" style="padding: 6px 12px; display: block; color: var(--text-color);"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 6px; vertical-align: -2px;"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg> Live Activity</a></li>
								</ul>
							</div>
						</td>
					</tr>`;
				});

				html += '</tbody></table>';
				$wrapper.html(html);
				this.bind_app_row_actions(sites);

				// Re-apply active filter
				const activeFilter = this.$container.find('.app-filter-chip.active').data('filter') || 'all';
				if (activeFilter !== 'all') this.apply_app_filter(activeFilter);
			},
		});
	}

	bind_app_row_actions(sites) {
		const self = this;

		const bench_options = self.benches ? self.benches.map(b => ({ label: b.is_host ? `${b.name} (host)` : b.name, value: b.path })) : [];

		this.$container.find('.app-install').on('click', function (e) {
			e.preventDefault();
			const app = $(this).data('app');
			const $row = $(this).closest('tr');
			const d = new frappe.ui.Dialog({
				title: `Install ${app}`,
				fields: [
					{ 
						label: 'Target Bench', 
						fieldname: 'bench_path', 
						fieldtype: 'Select', 
						options: bench_options, 
						default: self.current_bench_path, 
						reqd: 1,
						onchange: function() {
							const bench_path = this.get_value();
							const is_host = bench_path === (self.benches.find(b => b.is_host) || {}).path;
							const site_method = is_host ? 'bench_manager.api.list_sites' : 'bench_manager.api.list_bench_sites';
							const site_args = is_host ? {} : { bench_path: bench_path };
							frappe.call({
								method: site_method,
								args: site_args,
								callback: (r) => {
									const new_sites = (r.message || []).map((s) => s.site_name || s);
									d.set_df_property('site_name', 'options', new_sites);
									if (new_sites.length > 0) d.set_value('site_name', new_sites[0]);
								}
							});
						}
					},
					{ label: 'Select Site', fieldname: 'site_name', fieldtype: 'Select', options: sites, reqd: 1 }
				],
				primary_action_label: 'Install',
				primary_action(values) {
					d.hide();
					const is_host = values.bench_path === (self.benches.find(b => b.is_host) || {}).path;
					const bench_name = (self.benches.find(b => b.path === values.bench_path) || {}).name || 'host';

					self.append_console(`$ bench --site ${values.site_name} install-app ${app}`, 'command');
					self.append_console(`Installing ${app} on ${values.site_name} (${bench_name}). This may take a moment...`, 'stdout');
					self.append_console(`The app will be installed in the background. Watch the live activity for progress.`, 'info');
					self.show_live_activity(app);
					
					const install_method = is_host ? 'bench_manager.api.install_app' : 'bench_manager.api.install_app_on_site_remote';
					const install_args = { site_name: values.site_name, app_name: app };
					if (!is_host) install_args.bench_path = values.bench_path;

					frappe.call({
						method: install_method,
						args: install_args,
						callback: (r) => {
							if (r.message) frappe.show_alert({ message: r.message.message, indicator: 'blue' });
						},
					});
				},
			});
			d.show();
		});

		this.$container.find('.app-uninstall').on('click', function (e) {
			e.preventDefault();
			const app = $(this).data('app');
			
			const d = new frappe.ui.Dialog({
				title: `Uninstall ${app}`,
				fields: [
					{ 
						label: 'Target Bench', 
						fieldname: 'bench_path', 
						fieldtype: 'Select', 
						options: bench_options, 
						default: self.current_bench_path, 
						reqd: 1,
						onchange: function() {
							const bench_path = this.get_value();
							frappe.call({
								method: 'bench_manager.api.get_app_sites',
								args: { app_name: app, bench_path: bench_path },
								callback: (r) => {
									const installed_sites = r.message || [];
									d.set_df_property('site_name', 'options', installed_sites);
									if (installed_sites.length > 0) d.set_value('site_name', installed_sites[0]);
								}
							});
						}
					},
					{ 
						label: 'Select Site', 
						fieldname: 'site_name', 
						fieldtype: 'Select', 
						options: [], 
						reqd: 1,
						description: 'Only sites where this app is installed are listed.'
					}
				],
				primary_action_label: 'Uninstall from Site',
				primary_action(values) {
					d.hide();
					const is_host = values.bench_path === (self.benches.find(b => b.is_host) || {}).path;
					const bench_name = (self.benches.find(b => b.path === values.bench_path) || {}).name || 'host';

					frappe.confirm(`Uninstall <strong>${app}</strong> from <strong>${values.site_name}</strong>?`, () => {
						self.append_console(`$ bench --site ${values.site_name} uninstall-app ${app} --yes`, 'command');
						self.append_console(`Uninstalling ${app} from ${values.site_name} (${bench_name})...`, 'stdout');
						self.append_console(`The app will be removed in the background. Watch the live activity for progress.`, 'info');
						self.show_live_activity(app);
						
						const uninstall_method = is_host ? 'bench_manager.api.uninstall_app' : 'bench_manager.api.uninstall_app_from_site_remote';
						const uninstall_args = { site_name: values.site_name, app_name: app };
						if (!is_host) uninstall_args.bench_path = values.bench_path;

						frappe.call({
							method: uninstall_method,
							args: uninstall_args,
							callback: (r) => {
								if (r.message) frappe.show_alert({ message: r.message.message, indicator: 'orange' });
							},
						});
					});
				},
			});

			// Load initial sites for current bench
			frappe.call({
				method: 'bench_manager.api.get_app_sites',
				args: { app_name: app, bench_path: self.current_bench_path },
				callback: (r) => {
					const installed_sites = r.message || [];
					if (installed_sites.length > 0) {
						d.set_df_property('site_name', 'options', installed_sites);
						d.set_value('site_name', installed_sites[0]);
						d.show();
					} else {
						// App is not installed on any sites - show remove app from bench dialog
						frappe.confirm(`App <strong>${app}</strong> is not installed on any sites. Do you want to <strong>remove it from the bench entirely</strong>?`, () => {
							const is_host = self.current_bench_path === (self.benches.find(b => b.is_host) || {}).path;
							self.append_console(`$ bench remove-app ${app} --force`, 'command');
							self.append_console(`Removing app ${app} from bench...`, 'stdout');
							self.show_live_activity(app);
							
							const remove_method = is_host ? 'bench_manager.api.remove_app' : 'bench_manager.api.remove_app_remote';
							const remove_args = { app_name: app };
							if (!is_host) remove_args.bench_path = self.current_bench_path;

							frappe.call({
								method: remove_method,
								args: remove_args,
								callback: (r) => {
									if (r.message) {
										frappe.show_alert({ message: r.message.message, indicator: 'red' });
										setTimeout(() => self.load_apps(), 2000);
									}
								},
							});
						});
					}
				}
			});
		});

		this.$container.find('.app-sites').on('click', function (e) {
			e.preventDefault();
			const app = $(this).data('app');
			self.show_app_sites_modal(app);
		});

		this.$container.find('.app-live-activity').on('click', function (e) {
			e.preventDefault();
			const app = $(this).data('app');
			self.show_live_activity(app);
		});
	}

	add_pending_app_row(app_name, source, status_text) {
		const $tbody = this.$container.find('#apps-table-wrapper tbody');
		const badgeClass = source === 'frappe_store' ? 'badge-source-frappe' : (source === 'git' ? 'badge-source-git' : 'badge-source-custom');
		const badgeText = source === 'frappe_store' ? 'Frappe' : (source === 'git' ? 'Git' : 'Custom');
		
		const html = `<tr data-source="${source}" style="background-color: var(--highlight-color); animation: pulse 2s infinite;">
			<td>
				<div style="display:flex;flex-direction:column;gap:3px;">
					<strong>${frappe.utils.escape_html(app_name)}</strong>
					<span class="badge-source ${badgeClass}">${badgeText}</span>
				</div>
			</td>
			<td><small>—</small></td>
			<td>—</td>
			<td>
				<span class="text-warning" style="font-weight: 500; font-size: 12px;">
					<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation: spin-animation 2s linear infinite; margin-right: 4px; vertical-align: -2px;"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
					${status_text}
				</span>
			</td>
		</tr>`;
		$tbody.prepend(html);
	}

	show_app_sites_modal(app_name) {
		const self = this;
		const d = new frappe.ui.Dialog({
			title: `Sites with ${app_name}`,
			fields: [{ fieldtype: 'HTML', fieldname: 'sites_list' }],
		});
		
		d.$wrapper.find('[data-fieldname="sites_list"]').html(`<div class="text-center p-4 text-muted">Loading sites...</div>`);
		d.show();
		
		frappe.call({
			method: 'bench_manager.api.get_app_sites',
			args: { app_name: app_name },
			callback: (r) => {
				const sites = r.message || [];
				if (sites.length === 0) {
					d.$wrapper.find('[data-fieldname="sites_list"]').html(`<div class="text-center p-4 text-muted">This app is not installed on any sites.</div>`);
				} else {
					let html = `<ul class="list-group" style="margin:0; border-radius: 6px; overflow: hidden; border: 1px solid var(--border-color);">`;
					sites.forEach(site => {
						html += `<li class="list-group-item" style="border-left:0; border-right:0; border-top:0; border-bottom: 1px solid var(--border-color); padding: 12px 16px; display: flex; align-items: center; gap: 10px;">
							<div style="width: 32px; height: 32px; border-radius: 6px; background-color: var(--fg-color); display: flex; align-items: center; justify-content: center; border: 1px solid var(--border-color);">
								<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--text-color)" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
							</div>
							<strong>${frappe.utils.escape_html(site)}</strong>
							<button class="btn btn-xs btn-danger btn-uninstall-from-site" data-site="${frappe.utils.escape_html(site)}" style="margin-left: auto; padding: 4px 10px; font-weight: 500; border-radius: 4px;">Uninstall</button>
						</li>`;
					});
					html += `</ul>`;
					d.$wrapper.find('[data-fieldname="sites_list"]').html(html);
					
					// Bind uninstall event
					d.$wrapper.find('.btn-uninstall-from-site').on('click', (e) => {
						const site = $(e.currentTarget).data('site');
						d.hide();
						frappe.confirm(`Uninstall <strong>${app_name}</strong> from <strong>${site}</strong>?`, () => {
							self.append_console(`$ bench --site ${site} uninstall-app ${app_name} --yes`, 'command');
							self.append_console(`Uninstalling ${app_name} from ${site}...`, 'stdout');
							self.append_console(`The app will be removed in the background. Watch the live activity for progress.`, 'info');
							self.show_live_activity(app_name);
							frappe.call({
								method: 'bench_manager.api.uninstall_app',
								args: { site_name: site, app_name: app_name },
								callback: (r) => {
									if (r.message) frappe.show_alert({ message: r.message.message, indicator: 'orange' });
								},
							});
						});
					});
				}
			}
		});
	}

	setup_app_actions() {
		this.$container.find('#btn-new-app').on('click', () => this.show_new_app_dialog());
		this.$container.find('#btn-refresh-apps').on('click', () => this.load_apps());

		// Category filter chips
		this.$container.find('.app-filter-chip').on('click', (e) => {
			const $chip = $(e.currentTarget);
			this.$container.find('.app-filter-chip').removeClass('active');
			$chip.addClass('active');
			this.apply_app_filter($chip.data('filter'));
		});
	}

	apply_app_filter(filter) {
		const $rows = this.$container.find('#apps-table-wrapper tbody tr');
		if (filter === 'all') { $rows.show(); return; }
		$rows.each(function () {
			$(this).toggle(($(this).data('source') || 'custom') === filter);
		});
	}

	show_new_app_dialog() {
		const self = this;
		const FRAPPE_APPS = [
			{ id:'erpnext', name:'ERPNext', desc:'Full-featured open-source ERP for accounting, inventory, manufacturing, HR, and CRM.', repo:'https://github.com/frappe/erpnext.git', icon:'E', color:'#2490ef' },
			{ id:'builder', name:'Frappe Builder', desc:'Visual no-code website builder with drag-and-drop blocks and dynamic data binding.', repo:'https://github.com/frappe/builder.git', icon:'B', color:'#1657A1' },
			{ id:'crm', name:'Frappe CRM', desc:'Modern, open-source CRM with deal pipeline, email, calls, notes, and AI integration.', repo:'https://github.com/frappe/crm.git', icon:'C', color:'#D923B2' },
			{ id:'drive', name:'Frappe Drive', desc:'File storage and document management with sharing, permissions, and collaborative editing.', repo:'https://github.com/frappe/drive.git', icon:'D', color:'#1A737D' },
			{ id:'ecommerce', name:'eCommerce Integrations', desc:'Connectors for Shopify, WooCommerce, and other eCommerce platforms with ERPNext.', repo:'https://github.com/frappe/ecommerce_integrations.git', icon:'e', color:'#6A90E2' },
			{ id:'hrms', name:'Frappe HR', desc:'Modern HR and Payroll management software.', repo:'https://github.com/frappe/hrms.git', icon:'H', color:'#F1604B' },
			{ id:'lms', name:'Frappe LMS', desc:'Easy-to-use Learning Management System.', repo:'https://github.com/frappe/lms.git', icon:'L', color:'#1EAC77' },
			{ id:'helpdesk', name:'Frappe Helpdesk', desc:'Modern, streamlined customer support and issue tracking tool.', repo:'https://github.com/frappe/helpdesk.git', icon:'?', color:'#7C3AED' },
			{ id:'insights', name:'Frappe Insights', desc:'Open-source BI tool for data exploration, dashboards, and SQL queries.', repo:'https://github.com/frappe/insights.git', icon:'I', color:'#0EA5E9' },
			{ id:'wiki', name:'Frappe Wiki', desc:'Simple wiki for knowledge-base management.', repo:'https://github.com/frappe/wiki.git', icon:'W', color:'#475569' },
			{ id:'print_designer', name:'Print Designer', desc:'Drag-and-drop visual print format designer for Frappe.', repo:'https://github.com/frappe/print_designer.git', icon:'P', color:'#EC4899' },
			{ id:'lending', name:'Lending', desc:'Loan management application built on top of ERPNext.', repo:'https://github.com/frappe/lending.git', icon:'$', color:'#F59E0B' },
		];

		// Build store items HTML
		let storeItemsHtml = '';
		FRAPPE_APPS.forEach(app => {
			storeItemsHtml += `<div class="frappe-store-item" data-app-id="${app.id}" data-app-name="${app.name}" data-app-repo="${app.repo}">
				<div class="frappe-store-item-checkbox"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="3" style="display:none;"><polyline points="20 6 9 17 4 12"/></svg></div>
				<div class="frappe-store-item-icon" style="background:${app.color};">${app.icon}</div>
				<div class="frappe-store-item-info">
					<div class="frappe-store-item-name">${app.name}</div>
					<div class="frappe-store-item-desc">${app.desc}</div>
					<div class="frappe-store-item-meta">Official Frappe App</div>
				</div>
			</div>`;
		});

		const d = new frappe.ui.Dialog({
			title: 'Add New App',
			fields: [{
				fieldtype: 'HTML', fieldname: 'app_tabs',
				options: `
				<div class="new-app-dialog-tabs">
					<button class="new-app-dialog-tab active" data-tab="custom_app">Custom App</button>
					<button class="new-app-dialog-tab" data-tab="get_app">Get App</button>
					<button class="new-app-dialog-tab" data-tab="frappe_store">Frappe Store</button>
				</div>
				<div class="new-app-tab-pane active" id="dlg-tab-custom_app">
					<div class="form-group"><label class="control-label" style="font-size:12px;font-weight:600;">App Name <span class="text-danger">*</span></label><input type="text" class="form-control" id="custom_app_name" placeholder="snake_case, e.g., my_custom_app"></div>
					<div class="form-group"><label class="control-label" style="font-size:12px;font-weight:600;">App Title</label><input type="text" class="form-control" id="custom_app_title"></div>
					<div class="form-group"><label class="control-label" style="font-size:12px;font-weight:600;">Description</label><textarea class="form-control" id="custom_app_desc" rows="2"></textarea></div>
					<div style="display:flex;gap:12px;"><div style="flex:1;" class="form-group"><label class="control-label" style="font-size:12px;font-weight:600;">Publisher</label><input type="text" class="form-control" id="custom_app_publisher"></div><div style="flex:1;" class="form-group"><label class="control-label" style="font-size:12px;font-weight:600;">Email</label><input type="email" class="form-control" id="custom_app_email"></div></div>
				</div>
				<div class="new-app-tab-pane" id="dlg-tab-get_app">
					<div class="form-group"><label class="control-label" style="font-size:12px;font-weight:600;">Git URL <span class="text-danger">*</span></label><input type="text" class="form-control" id="get_app_url" placeholder="HTTPS or SSH URL"></div>
					<div class="form-group"><label class="control-label" style="font-size:12px;font-weight:600;">Branch</label><input type="text" class="form-control" id="get_app_branch" value="master"></div>
				</div>
				<div class="new-app-tab-pane" id="dlg-tab-frappe_store">
					<div class="frappe-store-search-wrap" style="position:relative;margin-bottom:12px;">
						<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="position:absolute;left:12px;top:50%;transform:translateY(-50%);color:#8898aa;pointer-events:none;"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
						<input type="text" class="frappe-store-search" placeholder="Search official apps..." style="width:100%;padding:9px 14px 9px 36px;border:1px solid #e2e8f0;border-radius:8px;font-size:13px;outline:none;">
					</div>
					<div class="frappe-store-list" style="max-height:320px;overflow-y:auto;border:1px solid #e2e8f0;border-radius:10px;">
						${storeItemsHtml}
					</div>
					<div style="padding:10px 14px;background:#f8fafc;border-top:1px solid #e2e8f0;border-radius:0 0 10px 10px;font-size:12px;font-weight:600;color:#8898aa;display:flex;align-items:center;justify-content:space-between;">
						<span id="store-selected-text">0 apps selected</span>
						<button class="btn btn-xs btn-default" id="store-clear-selection">Clear</button>
					</div>
				</div>`
			}],
			primary_action_label: 'Create App',
			primary_action() {
				const activeTab = d.$wrapper.find('.new-app-dialog-tab.active').data('tab');
				if (activeTab === 'custom_app') {
					const app_name = d.$wrapper.find('#custom_app_name').val();
					if (!app_name) { frappe.msgprint('App Name is required'); return; }
					d.hide();
					self.add_pending_app_row(app_name, 'custom', 'Creating app...');
					self.append_console(`$ bench new-app --no-git ${app_name}`, 'command');
					self.append_console(`Creating new Frappe app "${app_name}" on ${self.current_bench_name || 'host'}...`, 'stdout');
					self.append_console(`Scaffolding app directory, hooks, and module structure. Watch below for progress.`, 'info');
					self.show_live_activity(app_name);
					
					const create_method = self.is_host_bench ? 'bench_manager.api.create_new_app' : 'bench_manager.api.create_new_app_on_bench';
					const create_args = { app_name, title: d.$wrapper.find('#custom_app_title').val(), description: d.$wrapper.find('#custom_app_desc').val(), publisher: d.$wrapper.find('#custom_app_publisher').val(), email: d.$wrapper.find('#custom_app_email').val() };
					if (!self.is_host_bench) create_args.bench_path = self.current_bench_path;

					frappe.call({ method: create_method, args: create_args, callback: (r) => { if (r.message) frappe.show_alert({ message: r.message.message, indicator: 'blue' }); } });
				} else if (activeTab === 'get_app') {
					const git_url = d.$wrapper.find('#get_app_url').val();
					if (!git_url) { frappe.msgprint('Git URL is required'); return; }
					const branch = d.$wrapper.find('#get_app_branch').val() || 'master';
					// Extract a nice name from git url
					const parts = git_url.replace(/\.git$/, '').split('/');
					const nameGuess = parts[parts.length - 1];
					
					d.hide();
					self.add_pending_app_row(nameGuess, 'git', 'Cloning...');
					self.append_console(`$ bench get-app ${git_url} --branch ${branch}`, 'command');
					self.append_console(`Cloning repository and installing app on ${self.current_bench_name || 'host'}. This may take a few minutes...`, 'stdout');
					self.append_console(`The app will be cloned, dependencies installed, and assets built. Watch below for progress.`, 'info');
					self.show_live_activity('get-app');
					
					const get_method = self.is_host_bench ? 'bench_manager.api.get_app' : 'bench_manager.api.get_app_on_bench';
					const get_args = { git_url, branch };
					if (!self.is_host_bench) get_args.bench_path = self.current_bench_path;

					frappe.call({ method: get_method, args: get_args, callback: (r) => { if (r.message) frappe.show_alert({ message: r.message.message, indicator: 'blue' }); } });
				} else if (activeTab === 'frappe_store') {
					const sel = [];
					d.$wrapper.find('.frappe-store-item.selected').each(function () { sel.push({ name: $(this).data('app-name'), repo: $(this).data('app-repo') }); });
					if (!sel.length) { frappe.msgprint('Select at least one app'); return; }
					d.hide();
					sel.forEach(a => self.add_pending_app_row(a.name, 'frappe_store', 'Pending...'));
					
					self.append_console(`Installing ${sel.length} app${sel.length > 1 ? 's' : ''} from Frappe Store...`, 'stdout');
					self.append_console(`Apps: ${sel.map(a => a.name).join(', ')}`, 'info');
					let idx = 0;
					const next = () => {
						if (idx >= sel.length) {
							self.append_console(`All ${sel.length} Frappe Store apps have been queued for installation.`, 'success');
							return;
						}
						const a = sel[idx];
						self.append_console(`[${idx + 1}/${sel.length}] $ bench get-app ${a.repo}`, 'command');
						self.append_console(`Fetching ${a.name} onto ${self.current_bench_name || 'host'}...`, 'stdout');
						self.show_live_activity('frappe-store');
						
						const store_method = self.is_host_bench ? 'bench_manager.api.get_app' : 'bench_manager.api.get_app_on_bench';
						const store_args = { git_url: a.repo, branch: '' };
						if (!self.is_host_bench) store_args.bench_path = self.current_bench_path;

						frappe.call({
							method: store_method,
							args: store_args,
							callback: () => { idx++; setTimeout(next, 800); }
						});
					};
					next();
				}
			}
		});

		// Tab switching
		d.$wrapper.find('.new-app-dialog-tab').on('click', function () {
			const tab = $(this).data('tab');
			d.$wrapper.find('.new-app-dialog-tab').removeClass('active');
			$(this).addClass('active');
			d.$wrapper.find('.new-app-tab-pane').removeClass('active');
			d.$wrapper.find(`#dlg-tab-${tab}`).addClass('active');
			const labels = { custom_app: 'Create App', get_app: 'Get App', frappe_store: 'Install Selected' };
			d.$wrapper.find('.btn-primary-dark, .btn-primary').filter('.modal-footer .btn').text(labels[tab] || 'Submit');
			try { d.get_primary_btn().text(labels[tab]); } catch(e) {}
		});

		// Store item selection
		const updateCount = () => { const c = d.$wrapper.find('.frappe-store-item.selected').length; d.$wrapper.find('#store-selected-text').text(`${c} app${c!==1?'s':''} selected`); };
		d.$wrapper.find('.frappe-store-item').on('click', function () {
			$(this).toggleClass('selected');
			$(this).find('.frappe-store-item-checkbox svg').toggle($(this).hasClass('selected'));
			updateCount();
		});
		d.$wrapper.find('#store-clear-selection').on('click', function (e) { e.preventDefault(); d.$wrapper.find('.frappe-store-item').removeClass('selected').find('.frappe-store-item-checkbox svg').hide(); updateCount(); });

		// Search
		d.$wrapper.find('.frappe-store-search').on('input', function () {
			const t = $(this).val().toLowerCase();
			d.$wrapper.find('.frappe-store-item').each(function () { $(this).toggle($(this).data('app-name').toLowerCase().includes(t) || $(this).find('.frappe-store-item-desc').text().toLowerCase().includes(t)); });
		});

		d.show();
		d.$wrapper.find('.modal-dialog').css('max-width', '640px');
	}

	// ─── Bench Tab ───────────────────────────────────────────────

	load_bench_info() {
		// Load versions
		frappe.call({
			method: 'bench_manager.api.get_bench_version',
			callback: (r) => {
				if (r.message) {
					const v = r.message;
					const $body = this.$container.find('#version-info');
					$body.html(`
						<div class="version-row"><span class="version-label">Bench</span><span class="version-value">${frappe.utils.escape_html(v.bench || 'Unknown')}</span></div>
						<div class="version-row"><span class="version-label">Frappe</span><span class="version-value">${frappe.utils.escape_html(v.frappe || 'Unknown')}</span></div>
						<div class="version-row"><span class="version-label">Python</span><span class="version-value">${frappe.utils.escape_html(v.python || 'Unknown')}</span></div>
						<div class="version-row"><span class="version-label">Node.js</span><span class="version-value">${frappe.utils.escape_html(v.node || 'Unknown')}</span></div>
					`);
				}
			},
		});
	}

	// Helper: Show error in the VS Code blob tab (safe for cross-origin)
	_showEditorTabError(tab, message) {
		try {
			if (tab && !tab.closed) {
				tab.document.body.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100vh;background:#1a1a1a;margin:0;">
					<h2 style="color:#ff6b6b;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-weight:400;font-size:16px;text-align:center;max-width:500px;line-height:1.6;">${message}</h2>
				</div>`;
			}
		} catch (e) {
			// Cross-origin or tab already navigated — just try to close
			try { if (tab && !tab.closed) tab.close(); } catch (_) {}
		}
	}

	setup_bench_actions() {
		const self = this;

		this.$container.find('#btn-launch-vscode').on('click', (e) => {
			if (!this.current_bench_path) return;
			this.launch_vscode_with_button(this.current_bench_path, $(e.currentTarget));
		});

		this.$container.find('#btn-bench-update').on('click', () => {
			frappe.confirm('Run <strong>bench update</strong>? This will update all apps.', () => {
				self.append_console('Running bench update...', 'command');
				frappe.call({
					method: 'bench_manager.api.update_bench',
					callback: (r) => {
						if (r.message) frappe.show_alert({ message: r.message.message, indicator: 'blue' });
					},
				});
			});
		});

		this.$container.find('#btn-bench-migrate').on('click', () => {
			frappe.confirm('Run <strong>bench migrate</strong> on all sites?', () => {
				self.append_console('Running bench migrate (all sites)...', 'command');
				frappe.call({
					method: 'bench_manager.api.bench_migrate_all',
					callback: (r) => {
						if (r.message) frappe.show_alert({ message: r.message.message, indicator: 'blue' });
					},
				});
			});
		});
	}

	// ─── Logs Tab ────────────────────────────────────────────────

	load_logs() {
		const $wrapper = this.$container.find('#logs-table-wrapper');
		$wrapper.html('<div class="loading-placeholder">Loading logs...</div>');

		frappe.call({
			method: 'bench_manager.api.get_command_logs',
			args: { limit: 50 },
			callback: (r) => {
				if (r.message && r.message.length) {
					this.render_logs_table(r.message);
				} else {
					$wrapper.html(`
						<div class="empty-state">
							<img src="/assets/bench_manager/images/empty_benches.png" alt="Empty Logs" style="max-width: 140px; margin-bottom: 20px; opacity: 0.4;">
							<p>No command logs yet.</p>
						</div>
					`);
				}
			},
		});
	}

	render_logs_table(logs) {
		const $wrapper = this.$container.find('#logs-table-wrapper');
		let html = `<table class="bench-table">
			<thead><tr>
				<th>Time</th><th>Command</th><th>Status</th><th>User</th><th>Details</th>
			</tr></thead><tbody>`;

		logs.forEach((log) => {
			const badgeClass = `badge-${(log.status || 'unknown').toLowerCase()}`;
			const time = frappe.datetime.prettyDate(log.creation);
			const command = (log.command || '').length > 60
				? log.command.substring(0, 60) + '...'
				: log.command || '';

			html += `<tr>
				<td><small>${frappe.utils.escape_html(time)}</small></td>
				<td><code style="font-size:11px;">${frappe.utils.escape_html(command)}</code></td>
				<td><span class="badge-status ${badgeClass}">${frappe.utils.escape_html(log.status || 'Unknown')}</span></td>
				<td><small>${frappe.utils.escape_html(log.executed_by || '—')}</small></td>
				<td><a href="/app/bench-command-log/${log.name}" class="btn btn-xs btn-default">View</a></td>
			</tr>`;
		});

		html += '</tbody></table>';
		$wrapper.html(html);
	}

	setup_log_actions() {
		const self = this;

		this.$container.find('#btn-refresh-logs').on('click', () => self.load_logs());

		this.$container.find('#btn-clear-logs').on('click', () => {
			frappe.confirm('Clear all command logs?', () => {
				frappe.call({
					method: 'bench_manager.api.clear_logs',
					callback: (r) => {
						if (r.message) {
							frappe.show_alert({ message: r.message.message, indicator: 'green' });
							self.load_logs();
						}
					},
				});
			});
		});
	}

	// ─── VS Code Tab ─────────────────────────────────────────────

	load_vscode_instances() {
		const $wrapper = this.$container.find('#vscode-table-wrapper');
		$wrapper.html(`
			<div class="loading-placeholder" style="display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; height: 100%;">
				<svg width="32" height="32" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg" style="animation: pulse 2s infinite; opacity: 0.7;">
					<mask id="vsc-mask-load" maskUnits="userSpaceOnUse" x="0" y="0" width="100" height="100">
						<path d="M70.9119 99.3171C72.4869 99.9307 74.2828 99.8914 75.8725 99.1264L96.4608 89.2197C98.6242 88.1787 100 85.9892 100 83.5872V16.4133C100 14.0113 98.6243 11.8218 96.4609 10.7808L75.8725 0.873756C73.7862 -0.130279 71.3446 0.115699 69.5135 1.44695L31.5418 33.4462L13.0853 19.5516C11.5478 18.3589 9.39544 18.4343 7.94345 19.7344L1.59371 25.5899C-0.138163 27.1527 -0.142613 29.8983 1.58412 31.4663L17.5753 45.8662L1.58412 60.2661C-0.142613 61.8341 -0.138163 64.5797 1.59371 66.1425L7.94345 71.998C9.39544 73.2981 11.5478 73.3735 13.0853 72.1808L31.5418 58.2862L69.5135 90.2854C69.9254 90.6397 70.3978 90.9235 70.9119 91.1154V99.3171ZM75.0152 27.2989L45.1091 50.0001L75.0152 72.7012V27.2989Z" fill="white"/>
					</mask>
					<g mask="url(#vsc-mask-load)">
						<path d="M96.4614 10.7962L75.8569 0.875542C73.4719 -0.272773 70.6217 0.211611 68.75 2.08333L1.29858 60.5765C-0.461825 62.1263 -0.421669 64.8866 1.38437 66.3893L7.81485 72.0865C9.28045 73.3626 11.4057 73.4104 12.9222 72.1979L91.1324 11.2015C93.9567 9.01654 98 11.0387 98 14.5789V14.4275C98 12.0243 96.6244 9.83383 94.4614 8.79288L96.4614 10.7962Z" fill="currentColor"/>
						<g filter="url(#vsc-shadow-load)">
							<path d="M96.4614 89.2038L75.8569 99.1245C73.4719 100.273 70.6217 99.7884 68.75 97.9167L1.29858 39.4235C-0.461825 37.8737 -0.421669 35.1134 1.38437 33.6107L7.81485 27.9135C9.28045 26.6374 11.4057 26.5896 12.9222 27.8021L91.1324 88.7985C93.9567 90.9835 98 88.9613 98 85.4211V85.5725C98 87.9757 96.6244 90.1662 94.4614 91.2071L96.4614 89.2038Z" fill="currentColor"/>
						</g>
						<g filter="url(#vsc-shadow2-load)">
							<path d="M75.8578 99.1263C73.4721 100.274 70.6219 99.7885 68.75 97.9166C71.0564 100.223 75 98.5895 75 95.3278V4.67213C75 1.41039 71.0564 -0.223106 68.75 2.08329C70.6219 0.211402 73.4721 -0.273666 75.8578 0.87367L96.4587 10.7804C98.6234 11.8218 100 14.0114 100 16.4134V83.5866C100 85.9886 98.6234 88.1782 96.4587 89.2196L75.8578 99.1263Z" fill="currentColor"/>
						</g>
						<rect x="0" y="0" width="100" height="100" fill="url(#vsc-gradient-load)" opacity="0.25"/>
					</g>
					<defs>
						<filter id="vsc-shadow-load"><feFlood flood-opacity="0" result="BackgroundImageFix"/><feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0.25 0"/><feBlend mode="normal" in2="BackgroundImageFix"/><feComposite in="SourceGraphic"/></filter>
						<filter id="vsc-shadow2-load"><feFlood flood-opacity="0" result="BackgroundImageFix"/><feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0.25 0"/><feBlend mode="normal" in2="BackgroundImageFix"/><feComposite in="SourceGraphic"/></filter>
						<linearGradient id="vsc-gradient-load" x1="50" y1="0" x2="50" y2="100" gradientUnits="userSpaceOnUse"><stop stop-color="white"/><stop offset="1" stop-color="white" stop-opacity="0"/></linearGradient>
					</defs>
				</svg>
				<span>Loading editors...</span>
			</div>
		`);

		frappe.call({
			method: 'bench_manager.api.get_running_vscode_instances',
			callback: (r) => {
				const instances = r.message || [];
				if (instances.length) {
					this.render_vscode_instances(instances);
				} else {
					$wrapper.html(`
						<div class="empty-state">
							<img src="/assets/bench_manager/images/empty_benches.png" alt="No VS Code instances" style="max-width: 140px; margin-bottom: 20px;">
							<p>No VS Code instances running.</p>
						</div>
					`);
				}
			}
		});
	}

	render_vscode_instances(instances) {
		const $wrapper = this.$container.find('#vscode-table-wrapper');
		let html = `<table class="bench-table">
			<thead><tr>
				<th>Bench</th><th>Port</th><th>PID</th><th>Status</th><th>Actions</th>
			</tr></thead><tbody>`;

		instances.forEach((inst) => {
			let dynamicUrl = inst.url;
			if (dynamicUrl) {
				try {
					let urlObj = new URL(dynamicUrl);
					if (['127.0.0.1', 'localhost', '0.0.0.0'].includes(urlObj.hostname)) {
						urlObj.hostname = window.location.hostname;
						dynamicUrl = urlObj.toString();
					}
				} catch (e) {}
			}
			html += `<tr>
				<td>
					<div style="display:flex;flex-direction:column;gap:3px;">
						<strong>${frappe.utils.escape_html(inst.bench_name)}</strong>
						<small style="color:var(--text-muted);">${frappe.utils.escape_html(inst.bench_path)}</small>
					</div>
				</td>
				<td><span class="badge-source badge-source-git" style="background:#f1f5f9;color:#475569;border-color:#e2e8f0;font-family:monospace;">${inst.port}</span></td>
				<td><small>${inst.pid}</small></td>
				<td><span class="badge-status badge-success" style="display:inline-flex;align-items:center;gap:4px;"><span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#10b981;"></span> Running</span></td>
				<td>
					<div style="display:flex;gap:8px;">
						<a href="${dynamicUrl}" target="_blank" class="btn btn-xs btn-primary" style="padding:4px 10px;">Open</a>
						<button class="btn btn-xs btn-danger btn-stop-vscode" data-pid="${inst.pid}" style="padding:4px 10px;">Stop</button>
					</div>
				</td>
			</tr>`;
		});

		html += '</tbody></table>';
		$wrapper.html(html);

		// Bind actions
		$wrapper.find('.btn-stop-vscode').on('click', (e) => {
			const pid = $(e.currentTarget).data('pid');
			frappe.confirm('Stop this VS Code editor?', () => {
				frappe.call({
					method: 'bench_manager.api.stop_code_server',
					args: { pid: pid },
					callback: (r) => {
						if (r.message && r.message.status === 'success') {
							frappe.show_alert({ message: r.message.message, indicator: 'green' });
							this.load_vscode_instances();
						} else {
							frappe.show_alert({ message: r.message ? r.message.message : 'Error stopping editor', indicator: 'red' });
						}
					}
				});
			});
		});
	}

	setup_vscode_actions() {
		const self = this;
		
		this.$container.find('#btn-refresh-vscode').on('click', () => self.load_vscode_instances());

		// Populate the bench dropdown for VS Code from existing context selector
		const populate_dropdown = () => {
			const $source = self.$container.find('#bench-context-select');
			const $target = self.$container.find('#vscode-bench-select');
			if ($source.find('option').length > 1 || ($source.find('option').length === 1 && $source.find('option').val() !== 'default')) {
				$target.html($source.html());
			}
		};

		// Run when tabs are clicked to ensure dropdown is up-to-date
		this.$container.find('.bench-tab[data-tab="vscode"]').on('click', populate_dropdown);

		this.$container.find('#btn-launch-vscode-new').on('click', (e) => {
			const benchPath = self.$container.find('#vscode-bench-select').val();
			if (!benchPath || benchPath === 'default') {
				frappe.msgprint('Please select a bench first.');
				return;
			}
			self.launch_vscode_with_button(benchPath, $(e.currentTarget));
		});
	}

	launch_vscode_with_button(benchPath, $btn) {
		if (!benchPath) return;
		if ($btn.hasClass('vscode-loading')) return;
		
		$btn.addClass('vscode-loading');
		
		const $wrapper = this.$container.find('#vscode-table-wrapper');
		$wrapper.html(`
			<div style="padding: 80px 20px; text-align: center; color: var(--text-muted); display: flex; flex-direction: column; align-items: center;">
				<div class="vscode-launch-btn vscode-loading" style="margin-bottom: 20px; pointer-events: none; border-color: transparent;">
					<svg width="20" height="20" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
						<mask id="vsc-mask-launch" maskUnits="userSpaceOnUse" x="0" y="0" width="100" height="100">
							<path d="M70.9119 99.3171C72.4869 99.9307 74.2828 99.8914 75.8725 99.1264L96.4608 89.2197C98.6242 88.1787 100 85.9892 100 83.5872V16.4133C100 14.0113 98.6243 11.8218 96.4609 10.7808L75.8725 0.873756C73.7862 -0.130279 71.3446 0.115699 69.5135 1.44695L31.5418 33.4462L13.0853 19.5516C11.5478 18.3589 9.39544 18.4343 7.94345 19.7344L1.59371 25.5899C-0.138163 27.1527 -0.142613 29.8983 1.58412 31.4663L17.5753 45.8662L1.58412 60.2661C-0.142613 61.8341 -0.138163 64.5797 1.59371 66.1425L7.94345 71.998C9.39544 73.2981 11.5478 73.3735 13.0853 72.1808L31.5418 58.2862L69.5135 90.2854C69.9254 90.6397 70.3978 90.9235 70.9119 91.1154V99.3171ZM75.0152 27.2989L45.1091 50.0001L75.0152 72.7012V27.2989Z" fill="white"/>
						</mask>
						<g mask="url(#vsc-mask-launch)">
							<path d="M96.4614 10.7962L75.8569 0.875542C73.4719 -0.272773 70.6217 0.211611 68.75 2.08333L1.29858 60.5765C-0.461825 62.1263 -0.421669 64.8866 1.38437 66.3893L7.81485 72.0865C9.28045 73.3626 11.4057 73.4104 12.9222 72.1979L91.1324 11.2015C93.9567 9.01654 98 11.0387 98 14.5789V14.4275C98 12.0243 96.6244 9.83383 94.4614 8.79288L96.4614 10.7962Z" fill="currentColor"/>
							<g filter="url(#vsc-shadow-launch)">
								<path d="M96.4614 89.2038L75.8569 99.1245C73.4719 100.273 70.6217 99.7884 68.75 97.9167L1.29858 39.4235C-0.461825 37.8737 -0.421669 35.1134 1.38437 33.6107L7.81485 27.9135C9.28045 26.6374 11.4057 26.5896 12.9222 27.8021L91.1324 88.7985C93.9567 90.9835 98 88.9613 98 85.4211V85.5725C98 87.9757 96.6244 90.1662 94.4614 91.2071L96.4614 89.2038Z" fill="currentColor"/>
							</g>
							<g filter="url(#vsc-shadow2-launch)">
								<path d="M75.8578 99.1263C73.4721 100.274 70.6219 99.7885 68.75 97.9166C71.0564 100.223 75 98.5895 75 95.3278V4.67213C75 1.41039 71.0564 -0.223106 68.75 2.08329C70.6219 0.211402 73.4721 -0.273666 75.8578 0.87367L96.4587 10.7804C98.6234 11.8218 100 14.0114 100 16.4134V83.5866C100 85.9886 98.6234 88.1782 96.4587 89.2196L75.8578 99.1263Z" fill="currentColor"/>
							</g>
							<rect x="0" y="0" width="100" height="100" fill="url(#vsc-gradient-launch)" opacity="0.25"/>
						</g>
						<defs>
							<filter id="vsc-shadow-launch"><feFlood flood-opacity="0" result="BackgroundImageFix"/><feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0.25 0"/><feBlend mode="normal" in2="BackgroundImageFix"/><feComposite in="SourceGraphic"/></filter>
							<filter id="vsc-shadow2-launch"><feFlood flood-opacity="0" result="BackgroundImageFix"/><feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0.25 0"/><feBlend mode="normal" in2="BackgroundImageFix"/><feComposite in="SourceGraphic"/></filter>
							<linearGradient id="vsc-gradient-launch" x1="50" y1="0" x2="50" y2="100" gradientUnits="userSpaceOnUse"><stop stop-color="white"/><stop offset="1" stop-color="white" stop-opacity="0"/></linearGradient>
						</defs>
					</svg>
				</div>
				<div style="font-size: 16px; font-weight: 600; color: var(--text-color);">Starting VS Code Editor...</div>
				<div style="font-size: 13px; margin-top: 6px; opacity: 0.8;">Configuring environment and establishing connection</div>
			</div>
		`);

		frappe.call({
			method: 'bench_manager.api.launch_code_server',
			args: { bench_path: benchPath },
			callback: (r) => {
				if (!r.message) {
					$btn.removeClass('vscode-loading');
					this.load_vscode_instances && this.load_vscode_instances();
					frappe.msgprint({ title: 'Error', message: 'No response from server.', indicator: 'red' });
					return;
				}
				const res = r.message;

				if (res.status === 'already_running') {
					$btn.removeClass('vscode-loading');
					this.load_vscode_instances && this.load_vscode_instances();
				} else if (res.status === 'launching') {
					const pollPort = res.port;
					let attempts = 0;
					const maxAttempts = 30;

					const pollReady = () => {
						attempts++;
						frappe.call({
							method: 'bench_manager.api.check_code_server_status',
							args: { port: pollPort },
							callback: (pr) => {
								if (pr.message && pr.message.running) {
									$btn.removeClass('vscode-loading');
									this.load_vscode_instances && this.load_vscode_instances();
								} else if (attempts < maxAttempts) {
									setTimeout(pollReady, 1500);
								} else {
									$btn.removeClass('vscode-loading');
									this.load_vscode_instances && this.load_vscode_instances();
									frappe.msgprint({ title: 'Timeout', message: 'Startup Timeout. Please try again.', indicator: 'orange' });
								}
							},
							error: () => {
								if (attempts < maxAttempts) {
									setTimeout(pollReady, 1500);
								} else {
									$btn.removeClass('vscode-loading');
									this.load_vscode_instances && this.load_vscode_instances();
								}
							}
						});
					};
					setTimeout(pollReady, 1500);
				} else if (res.status === 'not_installed') {
					$btn.removeClass('vscode-loading');
					this.load_vscode_instances && this.load_vscode_instances();
					frappe.msgprint({
						title: 'VS Code Not Installed',
						message: res.message.replace(/\n/g, '<br>'),
						indicator: 'orange'
					});
				} else {
					$btn.removeClass('vscode-loading');
					this.load_vscode_instances && this.load_vscode_instances();
					frappe.msgprint({ title: 'Error', message: res.message, indicator: 'red' });
				}
			},
			error: () => {
				$btn.removeClass('vscode-loading');
				this.load_vscode_instances && this.load_vscode_instances();
				frappe.msgprint({ title: 'Error', message: 'Failed to reach the server.', indicator: 'red' });
			}
		});
	}
}
