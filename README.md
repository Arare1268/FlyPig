# FlyPig (飛豬)

一個基於 Python 與 pymobiledevice3 的 iOS 虛擬定位工具，支援單點鎖定傳送與多點路徑模擬。

## 功能特點
- **穩定傳送**：解決 iOS 定位自動回彈的問題。
- **手動輸入**：支援直接輸入經緯度座標。
- **路徑規劃**：可在地圖上點擊多點，模擬走路/開車移動。

1.安裝套件：
pip install -r requirements.txt

2.特別注意 (針對 pymobiledevice3)：
由於此工具需要建立Tunnel來與 iOS 設備溝通，在執行時可能需要 sudo 權限。

3.啟動程式：
sudo python app.py

4.Running on http://127.0.0.1:

-----使用方法------

1.單點傳送

<img width="2764" height="1386" alt="image" src="https://github.com/user-attachments/assets/5dc70be6-e04b-4b64-b1d4-20f625059405" />
點選想要模擬的位置並點擊“執行傳送” 每次更改單點傳送都需要按停止按鈕才可再次按執行傳送


2.多點路徑模擬
<img width="2828" height="1316" alt="image" src="https://github.com/user-attachments/assets/2251599f-ad57-4cf0-9fa7-60fe115195dc" />
點選兩個以上的路徑點後按下“開始走路”就可以進行模擬，可更改模擬速度
