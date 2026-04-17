# 授权管理工具目录

## 知识库授权工具

请将 `hx_knowledge_license_gender` 程序放置在此目录下，或确保该程序在系统PATH中可用。

### 使用方法

- 生成 license
```shell
hx_knowledge_license_gender gen --json "{\"machinecode\":\"1234567890abcdefghijklmnopqrstuvwxyz\",\"vul_expire\":30,\"virus_expire\":60,\"rules_expire\":50}" -o "C:\\Users\\emory\\Desktop\\1.lic"
```

- 解密 license
```shell
hx_knowledge_license_gender dec -i "C:\\Users\\emory\\Desktop\\1.lic"
```

## 设备授权工具

设备授权通过SSH连接到远程服务器 `10.40.24.17` 执行 `lic_gen` 程序。

### 服务器配置
- 主机: 10.40.24.17
- 用户: tdhx
- 密码: tdhx@2017
- 程序路径: /home/tdhx/license/x64/lic_gen

### 使用方法
```shell
/home/tdhx/license/x64/lic_gen -j '{"name":"北京天地和兴科技股份有限公司武汉研发中心测试专用授权","mc":"18b10008f910f88a93b8b7a7f5c58eba"}' -p ./18b10008f910f88a93b8b7a7f5c58eba.lic
```
