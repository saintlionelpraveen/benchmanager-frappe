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
		this.console_collapsed = false;
		this.init();
	}

	init() {
		this.page.main.html(frappe.render_template('bench_dashboard'));
		this.$container = this.page.main.find('.bench-dashboard');
		this.setup_tabs();
		this.setup_console();
		this.setup_realtime();
		this.setup_site_actions();
		this.setup_app_actions();
		this.setup_bench_actions();
		this.setup_log_actions();
		this.load_status();
		this.load_sites();
	}

	// ─── Tab Management ──────────────────────────────────────────

	setup_tabs() {
		const self = this;
		this.$container.find('.bench-tab').on('click', function () {
			const tab = $(this).data('tab');
			self.$container.find('.bench-tab').removeClass('active');
			$(this).addClass('active');
			self.$container.find('.tab-pane').removeClass('active');
			self.$container.find(`#tab-${tab}`).addClass('active');

			// Load data for selected tab
			if (tab === 'sites') self.load_sites();
			else if (tab === 'apps') self.load_apps();
			else if (tab === 'bench') self.load_bench_info();
			else if (tab === 'logs') self.load_logs();
		});
	}

	// ─── Console ─────────────────────────────────────────────────

	setup_console() {
		const self = this;

		this.$container.find('#btn-clear-console').on('click', () => {
			this.$container.find('#console-output').html(
				'<div class="console-line console-info">Console cleared.</div>'
			);
		});

		this.$container.find('#btn-toggle-console').on('click', () => {
			self.console_collapsed = !self.console_collapsed;
			this.$container.find('.console-body').toggleClass('collapsed', self.console_collapsed);
		});
	}

	append_console(message, type = 'stdout') {
		const $console = this.$container.find('#console-output');
		const cssClass = `console-${type}`;
		const $line = $(`<div class="console-line ${cssClass}"></div>`).text(message);
		$console.append($line);
		$console.scrollTop($console[0].scrollHeight);
	}

	// ─── Real-time ───────────────────────────────────────────────

	setup_realtime() {
		frappe.realtime.on('bench_console', (data) => {
			if (data && data.message) {
				this.append_console(data.message, data.msg_type || 'stdout');
			}
		});
	}

	// ─── Status Bar ──────────────────────────────────────────────

	load_status() {
		frappe.call({
			method: 'bench_manager.api.get_bench_status',
			callback: (r) => {
				if (r.message) {
					const d = r.message;
					this.$container.find('#status-sites-count').text(d.sites_count);
					this.$container.find('#status-apps-count').text(d.apps_count);
					this.$container.find('#status-dev-mode').text(
						d.developer_mode ? 'Development' : 'Production'
					);
				}
			},
		});

		frappe.call({
			method: 'bench_manager.api.get_bench_version',
			callback: (r) => {
				if (r.message) {
					this.$container.find('#status-frappe-version').text(
						`v${r.message.frappe || 'Unknown'}`
					);
				}
			},
		});
	}

	// ─── Sites Tab ───────────────────────────────────────────────

	load_sites() {
		const $wrapper = this.$container.find('#sites-table-wrapper');
		$wrapper.html('<div class="loading-placeholder">Loading sites...</div>');

		frappe.call({
			method: 'bench_manager.api.list_sites',
			callback: (r) => {
				if (r.message && r.message.length) {
					this.render_sites_table(r.message);
				} else {
					$wrapper.html(`
						<div class="empty-state">
							<svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
								<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/>
								<path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
							</svg>
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
				<th>Site Name</th><th>Status</th><th>Database</th><th>Apps</th><th>Actions</th>
			</tr></thead><tbody>`;

		sites.forEach((site) => {
			const badgeClass = `badge-${(site.status || 'unknown').toLowerCase()}`;
			const appsList = (site.apps || []).join(', ') || '—';
			html += `<tr>
				<td><strong>${frappe.utils.escape_html(site.site_name)}</strong></td>
				<td><span class="badge-status ${badgeClass}">${frappe.utils.escape_html(site.status)}</span></td>
				<td>${frappe.utils.escape_html(site.db_name || '—')}</td>
				<td><small>${frappe.utils.escape_html(appsList)}</small></td>
				<td>
					<div class="action-btn-group">
						<button class="btn btn-xs btn-default site-migrate" data-site="${frappe.utils.escape_html(site.site_name)}">Migrate</button>
						<button class="btn btn-xs btn-default site-backup" data-site="${frappe.utils.escape_html(site.site_name)}">Backup</button>
						<button class="btn btn-xs btn-default site-maintenance" data-site="${frappe.utils.escape_html(site.site_name)}" data-mode="${site.status === 'Maintenance' ? '0' : '1'}">${site.status === 'Maintenance' ? 'Disable Maint.' : 'Maintenance'}</button>
						<button class="btn btn-xs btn-danger site-drop" data-site="${frappe.utils.escape_html(site.site_name)}">Drop</button>
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

		this.$container.find('.site-migrate').on('click', function () {
			const site = $(this).data('site');
			frappe.confirm(`Migrate site <strong>${site}</strong>?`, () => {
				self.append_console(`Migrating site ${site}...`, 'command');
				frappe.call({
					method: 'bench_manager.api.migrate_site',
					args: { site_name: site },
					callback: (r) => {
						if (r.message) frappe.show_alert({ message: r.message.message, indicator: 'blue' });
					},
				});
			});
		});

		this.$container.find('.site-backup').on('click', function () {
			const site = $(this).data('site');
			self.append_console(`Backing up site ${site}...`, 'command');
			frappe.call({
				method: 'bench_manager.api.backup_site',
				args: { site_name: site },
				callback: (r) => {
					if (r.message) frappe.show_alert({ message: r.message.message, indicator: 'blue' });
				},
			});
		});

		this.$container.find('.site-maintenance').on('click', function () {
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

		this.$container.find('.site-drop').on('click', function () {
			const site = $(this).data('site');
			frappe.confirm(
				`<span style="color:red;font-weight:bold;">DANGER:</span> This will permanently delete site <strong>${site}</strong> and all its data. Continue?`,
				() => {
					self.append_console(`Dropping site ${site}...`, 'command');
					frappe.call({
						method: 'bench_manager.api.drop_site',
						args: { site_name: site },
						callback: (r) => {
							if (r.message) {
								frappe.show_alert({ message: r.message.message, indicator: 'orange' });
								setTimeout(() => self.load_sites(), 3000);
							}
						},
					});
				}
			);
		});
	}

	setup_site_actions() {
		this.$container.find('#btn-new-site').on('click', () => this.show_new_site_dialog());
		this.$container.find('#btn-refresh-sites').on('click', () => {
			this.load_sites();
			this.load_status();
		});
	}

	show_new_site_dialog() {
		const self = this;
		const d = new frappe.ui.Dialog({
			title: 'Create New Site',
			fields: [
				{ label: 'Site Name', fieldname: 'site_name', fieldtype: 'Data', reqd: 1, description: 'e.g., mysite.localhost' },
				{ label: 'Admin Password', fieldname: 'admin_password', fieldtype: 'Password', reqd: 1 },
				{ label: 'Database Root Password', fieldname: 'db_password', fieldtype: 'Password', description: 'Leave blank if not required' },
			],
			primary_action_label: 'Create Site',
			primary_action(values) {
				d.hide();
				self.append_console(`Creating site ${values.site_name}...`, 'command');
				frappe.call({
					method: 'bench_manager.api.create_site',
					args: values,
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

		frappe.call({
			method: 'bench_manager.api.list_apps',
			callback: (r) => {
				if (r.message && r.message.length) {
					this.render_apps_table(r.message);
				} else {
					$wrapper.html(`
						<div class="empty-state">
							<svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
								<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>
								<rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>
							</svg>
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
		frappe.call({
			method: 'bench_manager.api.list_sites',
			callback: (r) => {
				const sites = (r.message || []).map((s) => s.site_name);
				let html = `<table class="bench-table">
					<thead><tr>
						<th>App Name</th><th>Git URL</th><th>Branch</th><th>Actions</th>
					</tr></thead><tbody>`;

				apps.forEach((app) => {
					const gitUrl = app.git_url ? `<a href="${frappe.utils.escape_html(app.git_url)}" target="_blank">${frappe.utils.escape_html(app.git_url)}</a>` : '—';
					html += `<tr>
						<td><strong>${frappe.utils.escape_html(app.app_name)}</strong></td>
						<td><small>${gitUrl}</small></td>
						<td>${frappe.utils.escape_html(app.branch || '—')}</td>
						<td>
							<div class="action-btn-group">
								<button class="btn btn-xs btn-primary app-install" data-app="${frappe.utils.escape_html(app.app_name)}">Install</button>
								<button class="btn btn-xs btn-default app-uninstall" data-app="${frappe.utils.escape_html(app.app_name)}">Uninstall</button>
							</div>
						</td>
					</tr>`;
				});

				html += '</tbody></table>';
				$wrapper.html(html);
				this.bind_app_row_actions(sites);
			},
		});
	}

	bind_app_row_actions(sites) {
		const self = this;

		this.$container.find('.app-install').on('click', function () {
			const app = $(this).data('app');
			const d = new frappe.ui.Dialog({
				title: `Install ${app}`,
				fields: [
					{
						label: 'Select Site',
						fieldname: 'site_name',
						fieldtype: 'Select',
						options: sites.join('\n'),
						reqd: 1,
					},
				],
				primary_action_label: 'Install',
				primary_action(values) {
					d.hide();
					self.append_console(`Installing ${app} on ${values.site_name}...`, 'command');
					frappe.call({
						method: 'bench_manager.api.install_app',
						args: { site_name: values.site_name, app_name: app },
						callback: (r) => {
							if (r.message) frappe.show_alert({ message: r.message.message, indicator: 'blue' });
						},
					});
				},
			});
			d.show();
		});

		this.$container.find('.app-uninstall').on('click', function () {
			const app = $(this).data('app');
			const d = new frappe.ui.Dialog({
				title: `Uninstall ${app}`,
				fields: [
					{
						label: 'Select Site',
						fieldname: 'site_name',
						fieldtype: 'Select',
						options: sites.join('\n'),
						reqd: 1,
					},
				],
				primary_action_label: 'Uninstall',
				primary_action(values) {
					d.hide();
					frappe.confirm(`Uninstall <strong>${app}</strong> from <strong>${values.site_name}</strong>?`, () => {
						self.append_console(`Uninstalling ${app} from ${values.site_name}...`, 'command');
						frappe.call({
							method: 'bench_manager.api.uninstall_app',
							args: { site_name: values.site_name, app_name: app },
							callback: (r) => {
								if (r.message) frappe.show_alert({ message: r.message.message, indicator: 'orange' });
							},
						});
					});
				},
			});
			d.show();
		});
	}

	setup_app_actions() {
		this.$container.find('#btn-new-app').on('click', () => this.show_new_app_dialog());
		this.$container.find('#btn-get-app').on('click', () => this.show_get_app_dialog());
		this.$container.find('#btn-refresh-apps').on('click', () => this.load_apps());
	}

	show_new_app_dialog() {
		const self = this;
		const d = new frappe.ui.Dialog({
			title: 'Create New App',
			fields: [
				{ label: 'App Name', fieldname: 'app_name', fieldtype: 'Data', reqd: 1, description: 'snake_case, e.g., my_custom_app' },
				{ label: 'App Title', fieldname: 'title', fieldtype: 'Data' },
				{ label: 'Description', fieldname: 'description', fieldtype: 'Small Text' },
				{ label: 'Publisher', fieldname: 'publisher', fieldtype: 'Data' },
				{ label: 'Email', fieldname: 'email', fieldtype: 'Data' },
			],
			primary_action_label: 'Create App',
			primary_action(values) {
				d.hide();
				self.append_console(`Creating app ${values.app_name}...`, 'command');
				frappe.call({
					method: 'bench_manager.api.create_new_app',
					args: values,
					callback: (r) => {
						if (r.message) {
							frappe.show_alert({ message: r.message.message, indicator: 'blue' });
							setTimeout(() => self.load_apps(), 3000);
						}
					},
				});
			},
		});
		d.show();
	}

	show_get_app_dialog() {
		const self = this;
		const d = new frappe.ui.Dialog({
			title: 'Get App from Git',
			fields: [
				{ label: 'Git URL', fieldname: 'git_url', fieldtype: 'Data', reqd: 1, description: 'HTTPS or SSH URL' },
				{ label: 'Branch', fieldname: 'branch', fieldtype: 'Data', default: 'master' },
			],
			primary_action_label: 'Get App',
			primary_action(values) {
				d.hide();
				self.append_console(`Fetching app from ${values.git_url}...`, 'command');
				frappe.call({
					method: 'bench_manager.api.get_app',
					args: values,
					callback: (r) => {
						if (r.message) {
							frappe.show_alert({ message: r.message.message, indicator: 'blue' });
							setTimeout(() => self.load_apps(), 5000);
						}
					},
				});
			},
		});
		d.show();
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

	setup_bench_actions() {
		const self = this;

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
							<svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
								<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
								<polyline points="14 2 14 8 20 8"/>
							</svg>
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
}
