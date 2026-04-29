
        // 实时时钟
        function updateClock() {
            const now = new Date();
            const time = now.toLocaleTimeString('zh-CN', { hour12: false });
            document.getElementById('clock').textContent = time;
        }
        updateClock();
        setInterval(updateClock, 1000);

        // API 请求封装
        async function apiRequest(url, method = 'GET', data = null) {
            const options = {
                method: method,
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken') || ''
                }
            };
            if (data) options.body = JSON.stringify(data);

            const response = await fetch(url, options);
            return response.json();
        }

        // 获取 Cookie
        function getCookie(name) {
            let cookieValue = null;
            if (document.cookie && document.cookie !== '') {
                const cookies = document.cookie.split(';');
                for (let i = 0; i < cookies.length; i++) {
                    const cookie = cookies[i].trim();
                    if (cookie.substring(0, name.length + 1) === (name + '=')) {
                        cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                        break;
                    }
                }
            }
            return cookieValue;
        }

        // Toast 提示函数
        function showToast(message, type = 'success') {
            const container = document.getElementById('toast-container');
            const toast = document.createElement('div');
            toast.className = 'toast ' + type;
            toast.textContent = message;
            container.appendChild(toast);

            setTimeout(() => {
                toast.style.opacity = '0';
                toast.style.transform = 'translateX(100px)';
                setTimeout(() => toast.remove(), 300);
            }, 3000);
        }

        // 确认弹窗函数
        let confirmCallback = null;

        function showConfirmModal(title, message, onConfirm, icon = '⚠️') {
            document.getElementById('confirm-title').textContent = title;
            document.getElementById('confirm-message').textContent = message;
            document.getElementById('confirm-icon').textContent = icon;
            confirmCallback = onConfirm;
            document.getElementById('confirm-modal').style.display = 'flex';
        }

        function hideConfirmModal() {
            document.getElementById('confirm-modal').style.display = 'none';
            confirmCallback = null;
        }

        document.getElementById('confirm-btn').addEventListener('click', function() {
            if (confirmCallback) {
                confirmCallback();
            }
            hideConfirmModal();
        });
    