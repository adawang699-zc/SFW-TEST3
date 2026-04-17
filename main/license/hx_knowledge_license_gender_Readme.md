# 天地和兴知识库授权生成及解密

## 使用方法

- 生成 license

  ```shell
  hx_knowledge_license_gender gen --json "{\"machinecode\":\"1234567890abcdefghijklmnopqrstuvwxyz\",\"vul_expire\":30,\"virus_expire\":60,\"rules_expire\":50}" -o "C:\\Users\\emory\\Desktop\\1.lic"
  
  选项：
      --josn        指定授权信息
      -o,--output   指定 license 生成路径
  
  说明：
  	参数使用 "" 包围
  	程序返回 0 表示成功
  ```

- 解密 license

  ```shell
  hx_knowledge_license_gender dec -i "C:\\Users\\emory\\Desktop\\1.lic"
  
  选项：
      -i,--input    指定 license 路径
  
  说明：
  	参数使用 "" 包围
  	程序返回 0 表示成功
  	授权信息通过控制台输出
  ```


## 返回值

   ```
   0xE1   json 解析错误
   0xE2   内存分配失败
   0xE3   文件打开失败
   0xE4   签名校验失败
   ```

   