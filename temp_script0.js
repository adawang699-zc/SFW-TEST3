
// 全局状态
let serverAgent = null;
let clientAgent = null;
let agents = [];
let serverProtocol = 'tcp';
let clientProtocol = 'tcp';
let mailAction = 'send';
let serverStatusInterval = null;
let clientStatusInterval = null;

// 页面加载
window.onload = function() {
    loadAgents();

    // 附件预览功能
    document.getElementById('mail-attachment').addEventListener('change', function(e) {
        const files = e.target.files;
        const previewArea = document.getElementById('mail-attachment-preview');
        const previewList = document.getElementById('mail-attachment-list');

        if (files && files.length > 0) {
            previewArea.style.display = 'block';
            let previewHtml = '';
            Array.from(files).forEach(file => {
                const size = file.size < 1024 ? file.size + ' B' :
                    file.size < 1024 * 1024 ? (file.size / 1024).toFixed(1) + ' KB' :
                    (file.size / 1024 / 1024).toFixed(1) + ' MB';
                previewHtml += `
                    <div class="connection-item">
                        <span class="connection-info">📎 ${file.name}</span>
                        <span class="connection-info">${size}</span>
                    </div>
                `;
            });
            previewList.innerHTML = previewHtml;
        } else {
            previewArea.style.display = 'none';
        }
    });
};

// 加载Agent列表
async function loadAgents() {
    try {
        const result = await apiRequest('/api/agents/my-rented/');
        console.log('API返回:', result);  // 调试日志
        const clientIpInfo = result.client_ip || '未知';
        console.log('客户端IP:', clientIpInfo, '是否有租用:', result.has_rental);

        if (!result.has_rental) {
            document.getElementById('server-agent').innerHTML = '<option value="">无可用Agent - ' + (result.message || '请先租用Agent') + '</option>';
            document.getElementById('client-agent').innerHTML = '<option value="">无可用Agent - ' + (result.message || '请先租用Agent') + '</option>';
            return;
        }

        agents = result.agents || [];
        console.log('Agent数量:', agents.length);
        agents.sort((a, b) => {
            const numA = parseInt(a.agent_id.replace('agent_eth', '')) || 0;
            const numB = parseInt(b.agent_id.replace('agent_eth', '')) || 0;
            return numA - numB;
        });

        // 填充下拉框
        let serverOptions = '<option value="">-- 选择Agent --</option>';
        let clientOptions = '<option value="">-- 选择Agent --</option>';

        agents.forEach(agent => {
            console.log('Agent:', agent.agent_id, 'has_ip:', agent.has_ip, 'ip:', agent.ip_address);
            // 只需要有 Agent ID 即可选择
            if (agent.agent_id) {
                const statusText = agent.status === 'running' ? '运行中' : '离线';
                const ipText = agent.ip_address ? `${agent.ip_address}:${agent.port}` : '无IP';
                serverOptions += `<option value="${agent.agent_id}">${agent.agent_id} (${ipText}) [${statusText}]</option>`;
                clientOptions += `<option value="${agent.agent_id}">${agent.agent_id} (${ipText}) [${statusText}]</option>`;
            }
        });

        document.getElementById('server-agent').innerHTML = serverOptions;
        document.getElementById('client-agent').innerHTML = clientOptions;

    } catch (e) {
        console.error('加载Agent失败:', e);
        addServerLog('加载Agent失败: ' + e.message, 'error');
        addClientLog('加载Agent失败: ' + e.message, 'error');
        document.getElementById('server-agent').innerHTML = '<option value="">加载失败: ' + e.message + '</option>';
        document.getElementById('client-agent').innerHTML = '<option value="">加载失败: ' + e.message + '</option>';
    }
}

// 服务端Agent选择
function onServerAgentChange() {
    const agentId = document.getElementById('server-agent').value;
    serverAgent = agents.find(a => a.agent_id === agentId);

    if (serverAgent) {
        document.getElementById('server-agent-info').textContent =
            `IP: ${serverAgent.ip_address}, Port: ${serverAgent.port}`;
        addServerLog(`选择服务端Agent: ${serverAgent.agent_id}`);
        refreshServerStatus();
        startServerStatusPoll();
    } else {
        document.getElementById('server-agent-info').textContent = '';
        stopServerStatusPoll();
    }
}

// 客户端Agent选择
function onClientAgentChange() {
    const agentId = document.getElementById('client-agent').value;
    clientAgent = agents.find(a => a.agent_id === agentId);

    if (clientAgent) {
        document.getElementById('client-agent-info').textContent =
            `IP: ${clientAgent.ip_address}, Port: ${clientAgent.port}`;
        addClientLog(`选择客户端Agent: ${clientAgent.agent_id}`);
        refreshClientStatus();
        startClientStatusPoll();
    } else {
        document.getElementById('client-agent-info').textContent = '';
        stopClientStatusPoll();
    }
}

// 同步服务端信息到客户端
function syncServerInfo() {
    if (!serverAgent) {
        showToast('请先选择服务端Agent', 'error');
        return;
    }

    // 填充客户端目标地址
    document.getElementById('tcp-client-ip').value = serverAgent.ip_address;
    document.getElementById('tcp-client-port').value = document.getElementById('tcp-server-port').value;
    document.getElementById('udp-client-ip').value = serverAgent.ip_address;
    document.getElementById('udp-client-port').value = document.getElementById('udp-server-port').value;
    document.getElementById('ftp-client-ip').value = serverAgent.ip_address;
    document.getElementById('ftp-client-port').value = document.getElementById('ftp-server-port').value;
    document.getElementById('http-client-ip').value = serverAgent.ip_address;
    document.getElementById('http-client-port').value = document.getElementById('http-server-port').value;
    document.getElementById('smtp-server').value = serverAgent.ip_address;
    document.getElementById('imap-server').value = serverAgent.ip_address;

    addClientLog('同步服务端信息: ' + serverAgent.ip_address);
    showToast('服务端信息已同步');
}

// 协议选择
function selectServerProtocol(protocol, element) {
    serverProtocol = protocol;
    document.querySelectorAll('#server-protocols .protocol-tab').forEach(tab => {
        tab.classList.remove('active');
    });
    element.classList.add('active');

    // 显示对应配置
    document.querySelectorAll('.config-section').forEach(section => {
        if (section.id.endsWith('-server-config')) {
            section.classList.add('hidden');
        }
    });
    document.getElementById(`${protocol}-server-config`).classList.remove('hidden');
}

function selectClientProtocol(protocol, element) {
    clientProtocol = protocol;
    document.querySelectorAll('#client-protocols .protocol-tab').forEach(tab => {
        tab.classList.remove('active');
    });
    element.classList.add('active');

    // 显示对应配置和操作按钮
    document.querySelectorAll('.config-section').forEach(section => {
        if (section.id.endsWith('-client-config')) {
            section.classList.add('hidden');
        }
    });
    document.querySelectorAll('.action-buttons').forEach(btns => {
        if (btns.id.endsWith('-client-actions')) {
            btns.style.display = 'none';
        }
    });

    document.getElementById(`${protocol}-client-config`).classList.remove('hidden');
    document.getElementById(`${protocol}-client-actions`).style.display = 'flex';
}

// Mail操作选择
function selectMailAction(action, element) {
    mailAction = action;
    document.querySelectorAll('.mail-tab').forEach(tab => {
        tab.classList.remove('active');
    });
    element.classList.add('active');

    document.getElementById('mail-send-config').style.display = action === 'send' ? 'block' : 'none';
    document.getElementById('mail-receive-config').style.display = action === 'receive' ? 'block' : 'none';
}

// ========== 服务端操作 ==========

async function startServerService() {
    if (!serverAgent) {
        showToast('请先选择服务端Agent', 'error');
        return;
    }

    const config = getServerConfig();
    config.agent_id = serverAgent.agent_id;

    addServerLog(`启动 ${serverProtocol.toUpperCase()} 监听...`);

    try {
        const result = await apiRequest('/api/services/listener/', 'POST', config);
        if (result.success) {
            showToast('监听启动成功');
            addServerLog(`${serverProtocol.toUpperCase()} 监听已启动`, 'info');
            document.getElementById('server-stop-btn').style.display = 'inline-block';
            refreshServerStatus();
        } else {
            showToast('启动失败: ' + result.error, 'error');
            addServerLog('启动失败: ' + result.error, 'error');
        }
    } catch (e) {
        showToast('请求失败: ' + e.message, 'error');
        addServerLog('请求失败: ' + e.message, 'error');
    }
}

async function stopServerService() {
    if (!serverAgent) return;

    const config = {
        agent_id: serverAgent.agent_id,
        protocol: serverProtocol,
        action: 'stop'
    };

    addServerLog(`停止 ${serverProtocol.toUpperCase()} 监听...`);

    try {
        const result = await apiRequest('/api/services/listener/', 'POST', config);
        if (result.success) {
            showToast('监听已停止');
            addServerLog(`${serverProtocol.toUpperCase()} 监听已停止`, 'info');
            document.getElementById('server-stop-btn').style.display = 'none';
            document.getElementById('server-connections-area').style.display = 'none';
        } else {
            showToast('停止失败: ' + result.error, 'error');
            addServerLog('停止失败: ' + result.error, 'error');
        }
    } catch (e) {
        showToast('请求失败: ' + e.message, 'error');
        addServerLog('请求失败: ' + e.message, 'error');
    }
}

function getServerConfig() {
    const config = {
        protocol: serverProtocol,
        action: 'start'
    };

    if (serverProtocol === 'tcp') {
        config.host = document.getElementById('tcp-server-host').value;
        config.port = parseInt(document.getElementById('tcp-server-port').value);
    } else if (serverProtocol === 'udp') {
        config.host = document.getElementById('udp-server-host').value;
        config.port = parseInt(document.getElementById('udp-server-port').value);
    } else if (serverProtocol === 'ftp') {
        config.host = document.getElementById('ftp-server-host').value;
        config.port = parseInt(document.getElementById('ftp-server-port').value);
        config.username = document.getElementById('ftp-server-user').value;
        config.password = document.getElementById('ftp-server-pass').value;
        config.directory = document.getElementById('ftp-server-dir').value;
    } else if (serverProtocol === 'http') {
        config.host = document.getElementById('http-server-host').value;
        config.port = parseInt(document.getElementById('http-server-port').value);
        config.directory = document.getElementById('http-server-dir').value;
    } else if (serverProtocol === 'mail') {
        config.smtp_port = parseInt(document.getElementById('smtp-port').value);
        config.imap_port = parseInt(document.getElementById('imap-port').value);
        config.pop3_port = parseInt(document.getElementById('pop3-port').value);
        config.domain = document.getElementById('mail-domain').value;
    }

    return config;
}

async function refreshServerStatus() {
    if (!serverAgent) return;

    try {
        const result = await apiRequest(`/api/services/status/?agent_id=${serverAgent.agent_id}`);
        if (result.success) {
            const listeners = result.listeners || {};
            const state = listeners[serverProtocol] || {};

            let statusHtml = `
                <div class="status-item">
                    <span class="status-label">状态</span>
                    <span class="status-value ${state.running ? 'running' : 'stopped'}">${state.running ? '运行中' : '已停止'}</span>
                </div>
            `;

            if (state.running) {
                statusHtml += `
                    <div class="status-item">
                        <span class="status-label">监听地址</span>
                        <span class="status-value">${state.host}:${state.port}</span>
                    </div>
                    <div class="status-item">
                        <span class="status-label">接收包数</span>
                        <span class="status-value">${state.packets || 0}</span>
                    </div>
                `;

                document.getElementById('server-stop-btn').style.display = 'inline-block';

                // TCP 连接列表（增强版）
                if (serverProtocol === 'tcp') {
                    const connTable = document.getElementById('tcp-connections-table');
                    const connList = document.getElementById('tcp-connections-list');
                    if (connTable) connTable.style.display = 'block';

                    if (state.connections && state.connections.length > 0) {
                        let connHtml = '';
                        state.connections.forEach(conn => {
                            connHtml += `
                                <div class="connection-item">
                                    <span class="connection-info">${conn.address || conn.id}</span>
                                    <span class="connection-info">${conn.bytes_received || 0} bytes</span>
                                    <button class="btn-xs btn-secondary" onclick="disconnectTcpConnection('${conn.id}')">断开</button>
                                </div>
                            `;
                        });
                        connList.innerHTML = connHtml;
                    } else {
                        connList.innerHTML = '<div style="color:var(--text-muted)">暂无连接</div>';
                    }
                } else {
                    const connTable = document.getElementById('tcp-connections-table');
                    if (connTable) connTable.style.display = 'none';
                }

                // HTTP 访问 URL 显示
                if (serverProtocol === 'http') {
                    const httpInfo = document.getElementById('http-server-info');
                    const httpUrl = document.getElementById('http-access-url');
                    if (httpInfo) httpInfo.style.display = 'block';

                    // 使用 Agent IP 替换 0.0.0.0
                    let displayHost = state.host || serverAgent.ip_address;
                    if (displayHost === '0.0.0.0') {
                        displayHost = serverAgent.ip_address;
                    }
                    const accessUrl = `http://${displayHost}:${state.port}`;
                    if (httpUrl) {
                        httpUrl.innerHTML = `<a href="${accessUrl}" target="_blank" style="color: #4CAF50;">${accessUrl}</a>`;
                    }
                } else {
                    const httpInfo = document.getElementById('http-server-info');
                    if (httpInfo) httpInfo.style.display = 'none';
                }

                // Mail 服务信息显示
                if (serverProtocol === 'mail') {
                    const mailInfo = document.getElementById('mail-server-info');
                    const mailUserMgmt = document.getElementById('mail-user-management');
                    if (mailInfo) mailInfo.style.display = 'block';
                    if (mailUserMgmt) mailUserMgmt.style.display = 'block';

                    let displayHost = state.host || serverAgent.ip_address;
                    if (displayHost === '0.0.0.0') {
                        displayHost = serverAgent.ip_address;
                    }
                    const domain = state.domain || document.getElementById('mail-domain').value;

                    document.getElementById('mail-smtp-url').textContent = `smtp://${displayHost}:${state.smtp_port || 25}`;
                    document.getElementById('mail-imap-url').textContent = `imap://${displayHost}:${state.imap_port || 143}`;
                    document.getElementById('mail-pop3-url').textContent = `pop3://${displayHost}:${state.pop3_port || 110}`;

                    // 加载邮件用户列表
                    loadMailUsers();
                } else {
                    const mailInfo = document.getElementById('mail-server-info');
                    const mailUserMgmt = document.getElementById('mail-user-management');
                    if (mailInfo) mailInfo.style.display = 'none';
                    if (mailUserMgmt) mailUserMgmt.style.display = 'none';
                }
            } else {
                document.getElementById('server-stop-btn').style.display = 'none';
                document.getElementById('tcp-connections-table')?.style.display = 'none';
                document.getElementById('http-server-info')?.style.display = 'none';
                document.getElementById('mail-server-info')?.style.display = 'none';
                document.getElementById('mail-user-management')?.style.display = 'none';
            }

            document.getElementById('server-status-content').innerHTML = statusHtml;
        }
    } catch (e) {
        addServerLog('状态刷新失败: ' + e.message, 'error');
    }
}

// 断开指定 TCP 连接
async function disconnectTcpConnection(connId) {
    if (!serverAgent) return;

    const config = {
        agent_id: serverAgent.agent_id,
        protocol: 'tcp',
        action: 'disconnect_connection',
        connection_id: connId
    };

    addServerLog(`断开连接: ${connId}...`);

    try {
        const result = await apiRequest('/api/services/listener/', 'POST', config);
        if (result.success) {
            showToast('连接已断开');
            addServerLog(`连接 ${connId} 已断开`, 'info');
            refreshServerStatus();
        } else {
            showToast('断开失败: ' + result.error, 'error');
        }
    } catch (e) {
        addServerLog('请求失败: ' + e.message, 'error');
    }
}

// 邮件用户管理
async function loadMailUsers() {
    if (!serverAgent) return;

    try {
        const result = await apiRequest(`/api/services/status/?agent_id=${serverAgent.agent_id}&detail=mail_users`);
        if (result.success && result.mail_users) {
            const usersList = document.getElementById('mail-users-list');
            if (usersList) {
                if (result.mail_users.length > 0) {
                    let usersHtml = '';
                    result.mail_users.forEach(user => {
                        usersHtml += `
                            <div class="connection-item">
                                <span class="connection-info">${user.username}</span>
                                <span class="connection-info">${user.email || '-'}</span>
                                <button class="btn-xs btn-secondary" onclick="deleteMailUser('${user.username}')">删除</button>
                            </div>
                        `;
                    });
                    usersList.innerHTML = usersHtml;
                } else {
                    usersList.innerHTML = '<div style="color:var(--text-muted)">暂无用户</div>';
                }
            }
        }
    } catch (e) {
        // 静默处理
    }
}

async function createMailUser() {
    if (!serverAgent) {
        showToast('请先选择服务端Agent并启动邮件服务', 'error');
        return;
    }

    const username = document.getElementById('mail-new-user').value.trim();
    const password = document.getElementById('mail-new-pass').value.trim();

    if (!username || !password) {
        showToast('请输入用户名和密码', 'error');
        return;
    }

    if (password.length < 4) {
        showToast('密码至少需要4位', 'error');
        return;
    }

    const config = {
        agent_id: serverAgent.agent_id,
        protocol: 'mail',
        action: 'create_user',
        username: username,
        password: password
    };

    addServerLog(`创建邮件用户: ${username}...`);

    try {
        const result = await apiRequest('/api/services/listener/', 'POST', config);
        if (result.success) {
            showToast('用户创建成功');
            addServerLog(`用户 ${username} 创建成功`, 'info');
            document.getElementById('mail-new-user').value = '';
            document.getElementById('mail-new-pass').value = '';
            loadMailUsers();
        } else {
            showToast('创建失败: ' + result.error, 'error');
            addServerLog('创建失败: ' + result.error, 'error');
        }
    } catch (e) {
        showToast('请求失败: ' + e.message, 'error');
        addServerLog('请求失败: ' + e.message, 'error');
    }
}

async function deleteMailUser(username) {
    if (!serverAgent) return;

    if (!confirm(`确定要删除用户 "${username}" 吗？`)) {
        return;
    }

    const config = {
        agent_id: serverAgent.agent_id,
        protocol: 'mail',
        action: 'delete_user',
        username: username
    };

    addServerLog(`删除邮件用户: ${username}...`);

    try {
        const result = await apiRequest('/api/services/listener/', 'POST', config);
        if (result.success) {
            showToast('用户已删除');
            addServerLog(`用户 ${username} 已删除`, 'info');
            loadMailUsers();
        } else {
            showToast('删除失败: ' + result.error, 'error');
        }
    } catch (e) {
        addServerLog('请求失败: ' + e.message, 'error');
    }
}

// ========== 客户端操作 ==========

async function connectTcp() {
    if (!clientAgent) {
        showToast('请先选择客户端Agent', 'error');
        return;
    }

    const config = {
        agent_id: clientAgent.agent_id,
        protocol: 'tcp',
        action: 'connect',
        config: {
            server_ip: document.getElementById('tcp-client-ip').value,
            server_port: parseInt(document.getElementById('tcp-client-port').value),
            connections: parseInt(document.getElementById('tcp-client-connections').value) || 1,
            connect_rate: parseFloat(document.getElementById('tcp-client-rate').value) || 1
        }
    };

    addClientLog(`连接 TCP ${config.config.server_ip}:${config.config.server_port} (${config.config.connections} 个连接, ${config.config.connect_rate} 个/秒)...`);

    try {
        const result = await apiRequest('/api/services/client/', 'POST', config);
        if (result.success) {
            showToast('TCP连接成功');
            addClientLog('TCP连接成功', 'info');
            document.getElementById('tcp-send-btn').style.display = 'inline-block';
            document.getElementById('tcp-disconnect-btn').style.display = 'inline-block';
            document.getElementById('tcp-client-table').style.display = 'block';

            // 显示客户端连接列表
            if (result.connections && result.connections.length > 0) {
                let connHtml = '';
                result.connections.forEach(conn => {
                    connHtml += `
                        <div class="connection-item">
                            <span class="connection-info">${conn.id || conn.address}</span>
                            <span class="connection-info">${conn.status || 'connected'}</span>
                        </div>
                    `;
                });
                document.getElementById('tcp-client-connections-list').innerHTML = connHtml;
            }
        } else {
            showToast('连接失败: ' + result.error, 'error');
            addClientLog('连接失败: ' + result.error, 'error');
        }
    } catch (e) {
        showToast('请求失败: ' + e.message, 'error');
        addClientLog('请求失败: ' + e.message, 'error');
    }
}

async function sendTcpData() {
    if (!clientAgent) return;

    const config = {
        agent_id: clientAgent.agent_id,
        protocol: 'tcp',
        action: 'start_send',
        config: {
            message: document.getElementById('tcp-client-data').value,
            interval: parseInt(document.getElementById('tcp-client-interval').value),
            count: parseInt(document.getElementById('tcp-client-count').value)
        }
    };

    addClientLog(`发送TCP数据...`);

    try {
        const result = await apiRequest('/api/services/client/', 'POST', config);
        if (result.success) {
            showToast('数据发送成功');
            addClientLog('数据发送成功', 'info');
        } else {
            showToast('发送失败: ' + result.error, 'error');
            addClientLog('发送失败: ' + result.error, 'error');
        }
    } catch (e) {
        addClientLog('请求失败: ' + e.message, 'error');
    }
}

async function disconnectTcp() {
    if (!clientAgent) return;

    const config = {
        agent_id: clientAgent.agent_id,
        protocol: 'tcp',
        action: 'disconnect'
    };

    addClientLog('断开TCP连接...');

    try {
        const result = await apiRequest('/api/services/client/', 'POST', config);
        if (result.success) {
            showToast('已断开连接');
            addClientLog('TCP已断开', 'info');
            document.getElementById('tcp-send-btn').style.display = 'none';
            document.getElementById('tcp-disconnect-btn').style.display = 'none';
            document.getElementById('tcp-client-table').style.display = 'none';
            document.getElementById('tcp-client-connections-list').innerHTML = '';
        } else {
            showToast('断开失败: ' + result.error, 'error');
        }
    } catch (e) {
        addClientLog('请求失败: ' + e.message, 'error');
    }
}

// UDP客户端
async function startUdpClient() {
    if (!clientAgent) {
        showToast('请先选择客户端Agent', 'error');
        return;
    }

    const config = {
        agent_id: clientAgent.agent_id,
        protocol: 'udp',
        action: 'start',
        config: {
            server_ip: document.getElementById('udp-client-ip').value,
            server_port: parseInt(document.getElementById('udp-client-port').value),
            message: document.getElementById('udp-client-data').value,
            interval: parseFloat(document.getElementById('udp-client-interval').value)
        }
    };

    addClientLog(`启动UDP客户端 ${config.config.server_ip}:${config.config.server_port}...`);

    try {
        const result = await apiRequest('/api/services/client/', 'POST', config);
        if (result.success) {
            showToast('UDP客户端启动成功');
            addClientLog('UDP客户端已启动', 'info');
            document.getElementById('udp-stop-btn').style.display = 'inline-block';
        } else {
            showToast('启动失败: ' + result.error, 'error');
            addClientLog('启动失败: ' + result.error, 'error');
        }
    } catch (e) {
        addClientLog('请求失败: ' + e.message, 'error');
    }
}

async function stopUdpClient() {
    if (!clientAgent) return;

    const config = {
        agent_id: clientAgent.agent_id,
        protocol: 'udp',
        action: 'stop'
    };

    addClientLog('停止UDP客户端...');

    try {
        const result = await apiRequest('/api/services/client/', 'POST', config);
        if (result.success) {
            showToast('UDP客户端已停止');
            addClientLog('UDP客户端已停止', 'info');
            document.getElementById('udp-stop-btn').style.display = 'none';
        } else {
            showToast('停止失败: ' + result.error, 'error');
        }
    } catch (e) {
        addClientLog('请求失败: ' + e.message, 'error');
    }
}

// FTP客户端
async function connectFtp() {
    if (!clientAgent) {
        showToast('请先选择客户端Agent', 'error');
        return;
    }

    const config = {
        agent_id: clientAgent.agent_id,
        protocol: 'ftp',
        action: 'connect',
        config: {
            server_ip: document.getElementById('ftp-client-ip').value,
            server_port: parseInt(document.getElementById('ftp-client-port').value),
            username: document.getElementById('ftp-client-user').value,
            password: document.getElementById('ftp-client-pass').value
        }
    };

    addClientLog(`连接FTP ${config.config.server_ip}:${config.config.server_port}...`);

    try {
        const result = await apiRequest('/api/services/client/', 'POST', config);
        if (result.success) {
            showToast('FTP连接成功');
            addClientLog('FTP连接成功', 'info');
            showFtpConnectedButtons();
        } else {
            showToast('连接失败: ' + result.error, 'error');
            addClientLog('连接失败: ' + result.error, 'error');
        }
    } catch (e) {
        addClientLog('请求失败: ' + e.message, 'error');
    }
}

function showFtpConnectedButtons() {
    document.getElementById('ftp-list-btn').style.display = 'inline-block';
    document.getElementById('ftp-upload-btn').style.display = 'inline-block';
    document.getElementById('ftp-download-btn').style.display = 'inline-block';
    document.getElementById('ftp-disconnect-btn').style.display = 'inline-block';
    document.getElementById('ftp-file-ops').style.display = 'block';
    document.getElementById('ftp-dir-ops').style.display = 'block';
}

function hideFtpConnectedButtons() {
    document.getElementById('ftp-list-btn').style.display = 'none';
    document.getElementById('ftp-upload-btn').style.display = 'none';
    document.getElementById('ftp-download-btn').style.display = 'none';
    document.getElementById('ftp-disconnect-btn').style.display = 'none';
    document.getElementById('ftp-file-ops').style.display = 'none';
    document.getElementById('ftp-dir-ops').style.display = 'none';
    document.getElementById('ftp-file-list').style.display = 'none';
}

async function listFtpFiles() {
    if (!clientAgent) return;

    const config = {
        agent_id: clientAgent.agent_id,
        protocol: 'ftp',
        action: 'list'
    };

    addClientLog('获取FTP文件列表...');

    try {
        const result = await apiRequest('/api/services/client/', 'POST', config);
        if (result.success) {
            addClientLog('获取文件列表成功', 'info');
            document.getElementById('ftp-file-list').style.display = 'block';
            let filesHtml = '';
            if (result.files) {
                result.files.forEach(file => {
                    filesHtml += `
                        <div class="file-item">
                            <span class="file-name">${file.name}</span>
                            <span class="file-size">${file.size || '-'}</span>
                        </div>
                    `;
                });
            }
            document.getElementById('ftp-files-content').innerHTML = filesHtml || '<div style="color:var(--text-muted)">无文件</div>';
        } else {
            addClientLog('获取失败: ' + result.error, 'error');
        }
    } catch (e) {
        addClientLog('请求失败: ' + e.message, 'error');
    }
}

async function uploadFtpFile() {
    if (!clientAgent) return;

    const filename = document.getElementById('ftp-file-name').value;
    if (!filename) {
        showToast('请输入文件名', 'error');
        return;
    }

    const config = {
        agent_id: clientAgent.agent_id,
        protocol: 'ftp',
        action: 'upload',
        config: {
            filename: filename,
            content: 'Test content for ' + filename
        }
    };

    addClientLog(`上传文件: ${filename}...`);

    try {
        const result = await apiRequest('/api/services/client/', 'POST', config);
        if (result.success) {
            showToast('文件上传成功');
            addClientLog('文件上传成功: ' + filename, 'info');
            listFtpFiles();
        } else {
            showToast('上传失败: ' + result.error, 'error');
            addClientLog('上传失败: ' + result.error, 'error');
        }
    } catch (e) {
        addClientLog('请求失败: ' + e.message, 'error');
    }
}

async function downloadFtpFile() {
    if (!clientAgent) return;

    const filename = document.getElementById('ftp-file-name').value;
    if (!filename) {
        showToast('请输入文件名', 'error');
        return;
    }

    const config = {
        agent_id: clientAgent.agent_id,
        protocol: 'ftp',
        action: 'download',
        config: {
            filename: filename
        }
    };

    addClientLog(`下载文件: ${filename}...`);

    try {
        const result = await apiRequest('/api/services/client/', 'POST', config);
        if (result.success) {
            showToast('文件下载成功');
            addClientLog(`下载成功: ${filename} (${result.file_size} bytes)`, 'info');
        } else {
            showToast('下载失败: ' + result.error, 'error');
            addClientLog('下载失败: ' + result.error, 'error');
        }
    } catch (e) {
        addClientLog('请求失败: ' + e.message, 'error');
    }
}

async function disconnectFtp() {
    if (!clientAgent) return;

    const config = {
        agent_id: clientAgent.agent_id,
        protocol: 'ftp',
        action: 'disconnect'
    };

    addClientLog('断开FTP连接...');

    try {
        const result = await apiRequest('/api/services/client/', 'POST', config);
        if (result.success) {
            showToast('FTP已断开');
            addClientLog('FTP已断开', 'info');
            hideFtpConnectedButtons();
        } else {
            showToast('断开失败: ' + result.error, 'error');
        }
    } catch (e) {
        addClientLog('请求失败: ' + e.message, 'error');
    }
}

// HTTP客户端
async function connectHttp() {
    if (!clientAgent) {
        showToast('请先选择客户端Agent', 'error');
        return;
    }

    const config = {
        agent_id: clientAgent.agent_id,
        protocol: 'http',
        action: 'connect',
        config: {
            server_ip: document.getElementById('http-client-ip').value,
            server_port: parseInt(document.getElementById('http-client-port').value)
        }
    };

    addClientLog(`连接HTTP ${config.config.server_ip}:${config.config.server_port}...`);

    try {
        const result = await apiRequest('/api/services/client/', 'POST', config);
        if (result.success) {
            showToast('HTTP连接成功');
            addClientLog('HTTP连接成功', 'info');
            showHttpConnectedButtons();
        } else {
            showToast('连接失败: ' + result.error, 'error');
            addClientLog('连接失败: ' + result.error, 'error');
        }
    } catch (e) {
        addClientLog('请求失败: ' + e.message, 'error');
    }
}

function showHttpConnectedButtons() {
    document.getElementById('http-list-btn').style.display = 'inline-block';
    document.getElementById('http-download-btn').style.display = 'inline-block';
    document.getElementById('http-disconnect-btn').style.display = 'inline-block';
    document.getElementById('http-file-ops').style.display = 'block';
}

function hideHttpConnectedButtons() {
    document.getElementById('http-list-btn').style.display = 'none';
    document.getElementById('http-download-btn').style.display = 'none';
    document.getElementById('http-disconnect-btn').style.display = 'none';
    document.getElementById('http-file-ops').style.display = 'none';
    document.getElementById('http-file-list').style.display = 'none';
}

async function listHttpFiles() {
    if (!clientAgent) return;

    const config = {
        agent_id: clientAgent.agent_id,
        protocol: 'http',
        action: 'list'
    };

    addClientLog('获取HTTP文件列表...');

    try {
        const result = await apiRequest('/api/services/client/', 'POST', config);
        if (result.success) {
            addClientLog('获取文件列表成功', 'info');
            document.getElementById('http-file-list').style.display = 'block';
            let filesHtml = '';
            if (result.files) {
                result.files.forEach(file => {
                    filesHtml += `<div class="file-item"><span class="file-name">${file}</span></div>`;
                });
            }
            document.getElementById('http-files-content').innerHTML = filesHtml || '<div style="color:var(--text-muted)">无文件</div>';
        } else {
            addClientLog('获取失败: ' + result.error, 'error');
        }
    } catch (e) {
        addClientLog('请求失败: ' + e.message, 'error');
    }
}

async function downloadHttpFile() {
    if (!clientAgent) return;

    const filename = document.getElementById('http-file-name').value;
    if (!filename) {
        showToast('请输入文件名', 'error');
        return;
    }

    const config = {
        agent_id: clientAgent.agent_id,
        protocol: 'http',
        action: 'download',
        config: {
            filename: filename
        }
    };

    addClientLog(`下载文件: ${filename}...`);

    try {
        const result = await apiRequest('/api/services/client/', 'POST', config);
        if (result.success) {
            showToast('文件下载成功');
            addClientLog(`下载成功: ${filename}`, 'info');
        } else {
            showToast('下载失败: ' + result.error, 'error');
        }
    } catch (e) {
        addClientLog('请求失败: ' + e.message, 'error');
    }
}

async function disconnectHttp() {
    if (!clientAgent) return;

    const config = {
        agent_id: clientAgent.agent_id,
        protocol: 'http',
        action: 'disconnect'
    };

    addClientLog('断开HTTP连接...');

    try {
        const result = await apiRequest('/api/services/client/', 'POST', config);
        if (result.success) {
            showToast('HTTP已断开');
            addClientLog('HTTP已断开', 'info');
            hideHttpConnectedButtons();
        } else {
            showToast('断开失败: ' + result.error, 'error');
        }
    } catch (e) {
        addClientLog('请求失败: ' + e.message, 'error');
    }
}

// Mail客户端
async function sendMail() {
    if (!clientAgent) {
        showToast('请先选择客户端Agent', 'error');
        return;
    }

    const mailData = {
        from: document.getElementById('mail-from').value,
        to: document.getElementById('mail-to').value,
        subject: document.getElementById('mail-subject').value,
        body: document.getElementById('mail-body').value
    };

    if (!mailData.to || !mailData.subject || !mailData.body) {
        showToast('请填写收件人、主题和正文', 'error');
        return;
    }

    // 处理附件
    const attachmentInput = document.getElementById('mail-attachment');
    const attachments = [];

    if (attachmentInput && attachmentInput.files && attachmentInput.files.length > 0) {
        addClientLog(`处理 ${attachmentInput.files.length} 个附件...`);

        // 读取所有附件文件
        const filePromises = Array.from(attachmentInput.files).map(file => {
            return new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = function(e) {
                    // 获取 base64 编码的内容（去掉前缀）
                    const base64Content = e.target.result.split(',')[1];
                    resolve({
                        filename: file.name,
                        content: base64Content,
                        type: file.type || 'application/octet-stream'
                    });
                };
                reader.onerror = function(e) {
                    reject(new Error('文件读取失败: ' + file.name));
                };
                reader.readAsDataURL(file);
            });
        });

        try {
            const processedAttachments = await Promise.all(filePromises);
            attachments.push(...processedAttachments);
            addClientLog(`附件处理完成: ${attachments.length} 个`, 'info');
        } catch (err) {
            showToast('附件处理失败: ' + err.message, 'error');
            addClientLog('附件处理失败: ' + err.message, 'error');
            return;
        }
    }

    const config = {
        agent_id: clientAgent.agent_id,
        protocol: 'mail',
        action: 'send_mail',
        smtp_config: {
            server: document.getElementById('smtp-server').value,
            port: parseInt(document.getElementById('smtp-server-port').value)
        },
        mail_data: mailData
    };

    // 如果有附件，添加到 mail_data
    if (attachments.length > 0) {
        config.mail_data.attachments = attachments;
    }

    addClientLog(`发送邮件: ${config.mail_data.subject}${attachments.length > 0 ? ' (含 ' + attachments.length + ' 个附件)' : ''}...`);

    try {
        const result = await apiRequest('/api/services/client/', 'POST', config);
        if (result.success) {
            showToast('邮件发送成功');
            addClientLog('邮件发送成功', 'info');
            // 清空附件选择
            document.getElementById('mail-attachment').value = '';
            document.getElementById('mail-attachment-preview').style.display = 'none';
        } else {
            showToast('发送失败: ' + result.error, 'error');
            addClientLog('发送失败: ' + result.error, 'error');
        }
    } catch (e) {
        addClientLog('请求失败: ' + e.message, 'error');
    }
}

async function receiveMails() {
    if (!clientAgent) return;

    const config = {
        agent_id: clientAgent.agent_id,
        protocol: 'mail',
        action: 'get_inbox',
        receive_config: {
            server: document.getElementById('imap-server').value,
            port: parseInt(document.getElementById('imap-server-port').value),
            username: document.getElementById('mail-user').value,
            password: document.getElementById('mail-password').value
        }
    };

    addClientLog('获取邮件...');

    try {
        const result = await apiRequest('/api/services/client/', 'POST', config);
        if (result.success) {
            addClientLog('获取邮件成功', 'info');
            document.getElementById('mail-list-area').style.display = 'block';
            let mailsHtml = '';
            if (result.mails) {
                result.mails.forEach(mail => {
                    mailsHtml += `
                        <div class="connection-item">
                            <span class="connection-info">${mail.mail_from}</span>
                            <span class="connection-info">${mail.subject || '无主题'}</span>
                        </div>
                    `;
                });
            }
            document.getElementById('mail-list-content').innerHTML = mailsHtml || '<div style="color:var(--text-muted)">无邮件</div>';
        } else {
            addClientLog('获取失败: ' + result.error, 'error');
        }
    } catch (e) {
        addClientLog('请求失败: ' + e.message, 'error');
    }
}

async function testMailConnection() {
    if (!clientAgent) return;

    const config = {
        agent_id: clientAgent.agent_id,
        protocol: 'mail',
        action: 'test_connection',
        type: 'smtp',
        config: {
            server: document.getElementById('smtp-server').value,
            port: parseInt(document.getElementById('smtp-server-port').value)
        }
    };

    addClientLog('测试邮件连接...');

    try {
        const result = await apiRequest('/api/services/client/', 'POST', config);
        if (result.success) {
            showToast('连接测试成功');
            addClientLog('邮件连接测试成功', 'info');
        } else {
            showToast('连接测试失败: ' + result.error, 'error');
            addClientLog('连接测试失败: ' + result.error, 'error');
        }
    } catch (e) {
        addClientLog('请求失败: ' + e.message, 'error');
    }
}

// Network测试
async function testNetworkPing() {
    if (!clientAgent) {
        showToast('请先选择客户端Agent', 'error');
        return;
    }

    const targetIp = document.getElementById('network-target-ip').value;
    if (!targetIp) {
        showToast('请输入目标IP', 'error');
        return;
    }

    const config = {
        agent_id: clientAgent.agent_id,
        protocol: 'network',
        action: 'ping',
        server: targetIp
    };

    addClientLog(`Ping测试: ${targetIp}...`);

    try {
        const result = await apiRequest('/api/services/client/', 'POST', config);
        if (result.success) {
            showToast('Ping测试成功');
            addClientLog('Ping测试成功', 'info');
        } else {
            showToast('Ping测试失败: ' + result.error, 'error');
            addClientLog('Ping测试失败: ' + result.error, 'error');
        }
    } catch (e) {
        addClientLog('请求失败: ' + e.message, 'error');
    }
}

async function testNetworkConnect() {
    if (!clientAgent) return;

    const targetIp = document.getElementById('network-target-ip').value;
    const targetPort = document.getElementById('network-target-port').value;

    if (!targetIp) {
        showToast('请输入目标IP', 'error');
        return;
    }

    const config = {
        agent_id: clientAgent.agent_id,
        protocol: 'network',
        action: 'ping',
        server: targetIp,
        port: targetPort ? parseInt(targetPort) : 0
    };

    addClientLog(`连通性测试: ${targetIp}:${targetPort || 'N/A'}...`);

    try {
        const result = await apiRequest('/api/services/client/', 'POST', config);
        if (result.success) {
            showToast('连通性测试成功');
            addClientLog('连通性测试成功', 'info');
        } else {
            showToast('连通性测试失败: ' + result.error, 'error');
            addClientLog('连通性测试失败: ' + result.error, 'error');
        }
    } catch (e) {
        addClientLog('请求失败: ' + e.message, 'error');
    }
}

// 状态轮询
function startServerStatusPoll() {
    if (serverStatusInterval) clearInterval(serverStatusInterval);
    serverStatusInterval = setInterval(refreshServerStatus, 3000);
}

function stopServerStatusPoll() {
    if (serverStatusInterval) {
        clearInterval(serverStatusInterval);
        serverStatusInterval = null;
    }
}

function startClientStatusPoll() {
    if (clientStatusInterval) clearInterval(clientStatusInterval);
    clientStatusInterval = setInterval(refreshClientStatus, 3000);
}

function stopClientStatusPoll() {
    if (clientStatusInterval) {
        clearInterval(clientStatusInterval);
        clientStatusInterval = null;
    }
}

async function refreshClientStatus() {
    if (!clientAgent) return;

    try {
        const result = await apiRequest(`/api/services/status/?agent_id=${clientAgent.agent_id}`);
        if (result.success) {
            const clients = result.clients || {};
            const state = clients[clientProtocol] || {};

            let statusHtml = `
                <div class="status-item">
                    <span class="status-label">状态</span>
                    <span class="status-value ${state.running ? 'running' : 'stopped'}">${state.running ? '运行中' : '已停止'}</span>
                </div>
            `;

            if (state.running) {
                statusHtml += `
                    <div class="status-item">
                        <span class="status-label">目标地址</span>
                        <span class="status-value">${state.server_ip}:${state.server_port}</span>
                    </div>
                    <div class="status-item">
                        <span class="status-label">发送间隔</span>
                        <span class="status-value">${state.send_interval || 0}ms</span>
                    </div>
                `;
            }

            document.getElementById('client-status-content').innerHTML = statusHtml;
        }
    } catch (e) {
        // 静默处理
    }
}

// API请求封装
async function apiRequest(url, method = 'GET', data = null) {
    const options = {
        method: method,
        headers: {'Content-Type': 'application/json'}
    };

    if (data) {
        options.body = JSON.stringify(data);
    }

    const response = await fetch(url, options);
    return response.json();
}

// 日志
function addServerLog(msg, level = 'info') {
    const log = document.getElementById('server-log-content');
    const time = new Date().toLocaleTimeString();
    const entry = document.createElement('div');
    entry.className = `log-entry log-${level}`;
    entry.textContent = `[${time}] ${msg}`;

    if (log.children.length === 1 && log.children[0].textContent === '等待操作...') {
        log.innerHTML = '';
    }

    log.appendChild(entry);
    log.scrollTop = log.scrollHeight;
}

function addClientLog(msg, level = 'info') {
    const log = document.getElementById('client-log-content');
    const time = new Date().toLocaleTimeString();
    const entry = document.createElement('div');
    entry.className = `log-entry log-${level}`;
    entry.textContent = `[${time}] ${msg}`;

    if (log.children.length === 1 && log.children[0].textContent === '等待操作...') {
        log.innerHTML = '';
    }

    log.appendChild(entry);
    log.scrollTop = log.scrollHeight;
}

function clearServerLogs() {
    document.getElementById('server-log-content').innerHTML = '<div style="color: var(--text-muted);">等待操作...</div>';
}

function clearClientLogs() {
    document.getElementById('client-log-content').innerHTML = '<div style="color: var(--text-muted);">等待操作...</div>';
}

// Toast提示
function showToast(msg, type = 'info') {
    const toast = document.createElement('div');
    toast.style.cssText = `
        position: fixed; top: 20px; right: 20px; padding: 12px 20px; border-radius: 6px;
        background: ${type === 'error' ? '#f44336' : '#4caf50'}; color: white; z-index: 9999;
    `;
    toast.textContent = msg;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}
