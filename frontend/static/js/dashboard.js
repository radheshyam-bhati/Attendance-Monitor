/**
 * PWIOI Attendance Monitor Dashboard JavaScript
 */

class Dashboard {
    constructor() {
        this.checkTime = '16:30';
        this.timezone = 'Asia/Kolkata';
        this.init();
    }

    async init() {
        this.setCurrentDate();
        await this.loadAllData();
        this.bindEvents();
        this.startAutoRefresh();
    }

    setCurrentDate() {
        const now = new Date();
        const options = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
        document.getElementById('current-date').textContent = now.toLocaleDateString('en-US', options);
    }

    startAutoRefresh() {
        // Refresh every 30 seconds
        setInterval(() => this.loadAllData(), 30000);
    }

    bindEvents() {
        // Check now button
        document.getElementById('check-now-btn').addEventListener('click', () => {
            this.triggerCheckNow();
        });

        // Schedule form
        const scheduleForm = document.getElementById('schedule-form');
        if (scheduleForm) {
            scheduleForm.addEventListener('submit', (e) => {
                e.preventDefault();
                this.saveScheduleSettings();
            });
        }
    }

    async loadAllData() {
        await Promise.all([
            this.loadLatestCheck(),
            this.loadHistory(),
            this.loadGmailStatus(),
            this.loadScheduleSettings(),
        ]);
    }

    async fetchJson(url, options = {}) {
        const response = await fetch(url, {
            headers: { 'Content-Type': 'application/json' },
            ...options,
        });
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: response.statusText }));
            throw new Error(error.detail || `HTTP ${response.status}: ${response.statusText}`);
        }
        return response.json();
    }

    // --- Schedule Settings ---

    async loadScheduleSettings() {
        try {
            const data = await this.fetchJson('/api/schedule');
            this.checkTime = data.check_time;
            this.timezone = data.timezone;
            this.renderScheduleSettings(data);
        } catch (error) {
            console.error('Failed to load schedule settings:', error);
        }
    }

    renderScheduleSettings(data) {
        // Update form fields
        document.getElementById('check-time').value = data.check_time;
        document.getElementById('timezone').value = data.timezone;

        // Update next check time
        const nextCheckEl = document.getElementById('next-check');
        const footerSchedule = document.getElementById('footer-schedule');
        const nextRun = data.next_scheduled_check;

        if (nextRun) {
            const nextDate = new Date(nextRun);
            const formatted = nextDate.toLocaleString('en-US', {
                weekday: 'short',
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
                timeZoneName: 'short',
            });
            const text = `Next check: <strong>${formatted}</strong>`;
            if (nextCheckEl) nextCheckEl.innerHTML = text;
            if (footerSchedule) footerSchedule.innerHTML = `${data.check_time} ${data.timezone} (Next: ${formatted})`;
        } else {
            const text = `Next check: <strong>Not scheduled</strong>`;
            if (nextCheckEl) nextCheckEl.innerHTML = text;
            if (footerSchedule) footerSchedule.innerHTML = `${data.check_time} ${data.timezone}`;
        }
    }

    async saveScheduleSettings() {
        const checkTime = document.getElementById('check-time').value;
        const timezone = document.getElementById('timezone').value;
        const btn = document.getElementById('save-schedule-btn');
        const btnText = btn.querySelector('.btn-text');
        const btnLoader = btn.querySelector('.btn-loader');
        const messageEl = document.getElementById('schedule-message');

        btn.disabled = true;
        btnText.textContent = 'Saving...';
        btnLoader.hidden = false;
        messageEl.textContent = '';
        messageEl.className = 'form-message';

        try {
            const data = await this.fetchJson('/api/schedule', {
                method: 'PUT',
                body: JSON.stringify({ check_time: checkTime, timezone }),
            });

            this.checkTime = data.check_time;
            this.timezone = data.timezone;
            this.renderScheduleSettings(data);

            messageEl.textContent = 'Schedule saved successfully!';
            messageEl.className = 'form-message success';
        } catch (error) {
            console.error('Failed to save schedule:', error);
            messageEl.textContent = `Failed to save: ${error.message}`;
            messageEl.className = 'form-message error';
        } finally {
            btn.disabled = false;
            btnText.textContent = 'Save Schedule';
            btnLoader.hidden = true;
        }
    }

    // --- Latest Check ---

    async loadLatestCheck() {
        try {
            const data = await this.fetchJson('/api/latest');
            this.renderTodayAttendance(data);
            this.renderStats(data);
            this.updateSchedulerStatus('success', 'Last check completed');
        } catch (error) {
            console.error('Failed to load latest check:', error);
            this.renderTodayAttendance(null);
            this.updateSchedulerStatus('idle', 'Scheduler: Idle');
        }
    }

    renderTodayAttendance(data) {
        const container = document.getElementById('today-attendance');

        if (!data || !data.subjects || data.subjects.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="icon">📭</div>
                    <p>No attendance data available yet.</p>
                    <p class="hint">Run a check or wait for the scheduled run at ${this.checkTime} ${this.timezone}.</p>
                </div>
            `;
            return;
        }

        // Group by period
        const byPeriod = {};
        for (const subj of data.subjects) {
            const period = subj.period || 0;
            if (!byPeriod[period]) byPeriod[period] = [];
            byPeriod[period].push(subj);
        }

        const periodLabels = {
            0: 'Unscheduled',
            1: 'Period 1',
            2: 'Period 2',
            3: 'Period 3',
            4: 'Period 4',
            5: 'Period 5',
            6: 'Period 6',
            7: 'Period 7',
            8: 'Period 8',
        };

        let html = '<div class="attendance-list">';

        for (const period of Object.keys(byPeriod).sort((a, b) => a - b)) {
            const subjects = byPeriod[period];
            const label = periodLabels[period] || `Period ${period}`;

            html += `
                <div class="attendance-group">
                    <div class="attendance-group-header">${label}</div>
                    <div class="attendance-items">
            `;

            for (const subj of subjects) {
                const statusClass = `status-${subj.status.toLowerCase()}`;
                const periodText = subj.period ? `Period ${subj.period}` : '—';
                html += `
                    <div class="attendance-item">
                        <span class="attendance-period">${periodText}</span>
                        <span class="attendance-subject">${this.escapeHtml(subj.subject_name)}</span>
                        <span class="attendance-status ${statusClass}">
                            ${this.getStatusIcon(subj.status)} ${subj.status}
                        </span>
                    </div>
                `;
            }

            html += '</div></div>';
        }

        html += '</div>';
        container.innerHTML = html;
    }

    renderStats(data) {
        const statsGrid = document.getElementById('stats-grid');
        if (!data || !data.subjects) {
            statsGrid.hidden = true;
            return;
        }

        const subjects = data.subjects;
        const present = subjects.filter(s => s.status === 'PRESENT').length;
        const absent = subjects.filter(s => s.status === 'ABSENT').length;
        const notMarked = subjects.filter(s => s.status === 'NOT_MARKED').length;
        const total = subjects.length;

        document.getElementById('stat-present').textContent = present;
        document.getElementById('stat-absent').textContent = absent;
        document.getElementById('stat-not-marked').textContent = notMarked;
        document.getElementById('stat-total').textContent = total;

        statsGrid.hidden = false;
    }

    // --- History ---

    async loadHistory() {
        try {
            const data = await this.fetchJson('/api/history?limit=30');
            this.renderHistory(data);
        } catch (error) {
            console.error('Failed to load history:', error);
            document.getElementById('history-body').innerHTML = `
                <tr><td colspan="8" class="loading-cell">Failed to load history</td></tr>
            `;
        }
    }

    renderHistory(data) {
        const tbody = document.getElementById('history-body');

        if (!data || data.length === 0) {
            tbody.innerHTML = `
                <tr><td colspan="8" class="loading-cell">No history available</td></tr>
            `;
            return;
        }

        tbody.innerHTML = data.map(check => `
            <tr>
                <td>${this.formatDate(check.check_date)}</td>
                <td>${this.formatDate(check.attendance_date)}</td>
                <td>${this.escapeHtml(check.batch || '—')}</td>
                <td><span class="badge badge-success">${check.present_count ?? '—'}</span></td>
                <td><span class="badge badge-danger">${check.absent_count ?? '—'}</span></td>
                <td>${check.subject_count}</td>
                <td>${this.renderEmailStatus(check.email_status)}</td>
                <td>
                    <button class="btn btn-secondary btn-sm" onclick="dashboard.viewDetail(${check.id})">
                        View
                    </button>
                </td>
            </tr>
        `).join('');
    }

    renderEmailStatus(status) {
        const statusMap = {
            'sent': '<span class="badge badge-success">✅ Sent</span>',
            'failed': '<span class="badge badge-danger">❌ Failed</span>',
            'pending': '<span class="badge badge-warning">⏳ Pending</span>',
        };
        return statusMap[status] || `<span class="badge">${this.escapeHtml(status)}</span>`;
    }

    async viewDetail(checkId) {
        try {
            const data = await this.fetchJson(`/api/history/${checkId}`);
            this.showDetailModal(data);
        } catch (error) {
            console.error('Failed to load detail:', error);
            alert('Failed to load details');
        }
    }

    showDetailModal(data) {
        // Simple modal implementation
        const modal = document.createElement('div');
        modal.className = 'modal-overlay';
        modal.innerHTML = `
            <div class="modal">
                <div class="modal-header">
                    <h3>Attendance Detail — ${this.formatDate(data.attendance_date)}</h3>
                    <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
                </div>
                <div class="modal-body">
                    <div class="detail-meta">
                        <p><strong>Check Date:</strong> ${this.formatDate(data.check_date)}</p>
                        <p><strong>Checked At:</strong> ${new Date(data.checked_at).toLocaleString()}</p>
                        <p><strong>Batch:</strong> ${this.escapeHtml(data.batch || 'N/A')}</p>
                        <p><strong>Present:</strong> ${data.present_count ?? 'N/A'}</p>
                        <p><strong>Absent:</strong> ${data.absent_count ?? 'N/A'}</p>
                        <p><strong>Email Status:</strong> ${this.renderEmailStatus(data.email_status)}</p>
                        ${data.email_sent_at ? `<p><strong>Email Sent:</strong> ${new Date(data.email_sent_at).toLocaleString()}</p>` : ''}
                        ${data.error_message ? `<p><strong>Error:</strong> ${this.escapeHtml(data.error_message)}</p>` : ''}
                    </div>
                    <h4>Subjects</h4>
                    <div class="attendance-list">
                        ${this.renderSubjectsForModal(data.subjects)}
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(modal);

        // Add modal styles if not present
        if (!document.getElementById('modal-styles')) {
            const style = document.createElement('style');
            style.id = 'modal-styles';
            style.textContent = `
                .modal-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 1000; padding: 20px; }
                .modal { background: white; border-radius: 8px; max-width: 600px; width: 100%; max-height: 80vh; overflow: auto; }
                .modal-header { display: flex; justify-content: space-between; align-items: center; padding: 16px; border-bottom: 1px solid var(--color-border); }
                .modal-close { background: none; border: none; font-size: 24px; cursor: pointer; color: var(--color-text-secondary); }
                .modal-body { padding: 16px; }
                .detail-meta p { margin: 8px 0; }
                .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 500; }
                .badge-success { background: #e6ffed; color: #28a745; }
                .badge-danger { background: #ffeef0; color: #cb2431; }
                .badge-warning { background: #fff5b1; color: #b36b00; }
            `;
            document.head.appendChild(style);
        }
    }

    renderSubjectsForModal(subjects) {
        if (!subjects || subjects.length === 0) {
            return '<p class="empty-state">No subjects</p>';
        }

        const byPeriod = {};
        for (const subj of subjects) {
            const period = subj.period || 0;
            if (!byPeriod[period]) byPeriod[period] = [];
            byPeriod[period].push(subj);
        }

        let html = '';
        for (const period of Object.keys(byPeriod).sort((a, b) => a - b)) {
            const label = period === '0' ? 'Unscheduled' : `Period ${period}`;
            html += `<div class="attendance-group"><div class="attendance-group-header">${label}</div><div class="attendance-items">`;
            for (const subj of byPeriod[period]) {
                const statusClass = `status-${subj.status.toLowerCase()}`;
                html += `
                    <div class="attendance-item">
                        <span class="attendance-subject">${this.escapeHtml(subj.subject_name)}</span>
                        <span class="attendance-status ${statusClass}">${this.getStatusIcon(subj.status)} ${subj.status}</span>
                    </div>
                `;
            }
            html += '</div></div>';
        }
        return html;
    }

    // --- Gmail Status ---

    async loadGmailStatus() {
        try {
            const data = await this.fetchJson('/api/gmail-status');
            this.renderGmailStatus(data);
        } catch (error) {
            console.error('Failed to load Gmail status:', error);
        }
    }

    renderGmailStatus(data) {
        const container = document.getElementById('gmail-status');

        if (data.authorized) {
            container.innerHTML = `
                <div class="gmail-status authorized">
                    <span class="icon">✅</span>
                    <div class="info">
                        <p><strong>Gmail Authorized</strong></p>
                        <p class="email">Reports will be sent to: ${this.escapeHtml(data.email)}</p>
                    </div>
                </div>
            `;
        } else {
            container.innerHTML = `
                <div class="gmail-status unauthorized">
                    <span class="icon">⚠️</span>
                    <div class="info">
                        <p><strong>Gmail Not Authorized</strong></p>
                        <p>Run <code>python run.py --authorize-gmail</code> to enable email reports.</p>
                    </div>
                </div>
            `;
        }
    }

    // --- Manual Check ---

    async triggerCheckNow() {
        const btn = document.getElementById('check-now-btn');
        const btnText = btn.querySelector('.btn-text');
        const btnLoader = btn.querySelector('.btn-loader');

        btn.disabled = true;
        btnText.textContent = 'Checking...';
        btnLoader.hidden = false;
        this.updateSchedulerStatus('running', 'Checking...');

        try {
            const response = await fetch('/api/check-now', { method: 'POST' });
            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || 'Check failed');
            }

            this.renderTodayAttendance(data);
            this.renderStats(data);
            await this.loadHistory();
            await this.loadGmailStatus();
            this.updateSchedulerStatus('success', 'Check completed');
        } catch (error) {
            console.error('Manual check failed:', error);
            this.updateSchedulerStatus('error', 'Check failed');
            alert(`Check failed: ${error.message}`);
        } finally {
            btn.disabled = false;
            btnText.textContent = 'Check Now';
            btnLoader.hidden = true;
        }
    }

    // --- Helpers ---

    updateSchedulerStatus(type, text) {
        const badge = document.getElementById('scheduler-status');
        badge.className = `status-badge status-${type}`;
        badge.textContent = text;
    }

    formatDate(dateStr) {
        if (!dateStr) return '—';
        const date = new Date(dateStr);
        return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    }

    getStatusIcon(status) {
        const icons = {
            'PRESENT': '✅',
            'ABSENT': '🔴',
            'NOT_MARKED': '🟡',
            'UNKNOWN': '❓',
        };
        return icons[status] || '❓';
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Initialize dashboard when DOM is ready
let dashboard;
document.addEventListener('DOMContentLoaded', () => {
    dashboard = new Dashboard();
});