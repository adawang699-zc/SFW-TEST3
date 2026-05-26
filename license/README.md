# 授权管理工具目录

## 知识库授权工具

Linux 环境自动使用 Python 版本（`hx_knowledge_license_gender.py`），Windows 使用 `.exe` 版本。

### 使用方法

- 生成 license
```shell
hx_knowledge_license_gender gen --json "{\"machinecode\":\"1234567890abcdefghijklmnopqrstuvwxyz\",\"vul_expire\":30,\"virus_expire\":60,\"rules_expire\":50}" -o /path/to/output.lic
```

- 解密 license
```shell
hx_knowledge_license_gender dec -i /path/to/license.lic
```

## 设备授权工具

设备授权在本地执行，需要将 `lic_gen`（DR 方式）和 `licgen`（dev-Code 方式）从 `10.40.24.17` 拷贝到此目录，并设置执行权限。

### 拷贝方法

```shell
# 从远程服务器拷贝到本地
scp tdhx@10.40.24.17:/home/tdhx/license/x64/lic_gen .
scp tdhx@10.40.24.17:/home/tdhx/license/x64/licgen .
scp tdhx@10.40.24.17:/usr/lib/libcrypto.so.1.0.0 /usr/lib/

# 设置执行权限
chmod +x lic_gen licgen
```

### 使用方法（DR 方式）
```shell
./lic_gen -j '{"name":"北京天地和兴科技股份有限公司武汉研发中心测试专用授权","mc":"18b10008f910f88a93b8b7a7f5c58eba"}' -p ./18b10008f910f88a93b8b7a7f5c58eba.lic
```

### 使用方法（dev-Code 方式）
```shell
./licgen -m 18b10008f910f88a93b8b7a7f5c58eba -p ./18b10008f910f88a93b8b7a7f5c58eba.lic
```
