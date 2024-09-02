const express = require('express');
const http = require('http');
const WebSocket = require('ws');
const cors = require('cors');
const fs = require('fs');
const path = require('path');
const app = express();
app.use(express.json());
app.use(cors());

const server = http.createServer(app);
const wss = new WebSocket.Server({ server });

let clients = [];
const dataFilePath = path.join(__dirname, 'sensor_data1.json');

// 데이터 파일이 존재하지 않으면 파일을 생성하고 빈 배열로 초기화
if (!fs.existsSync(dataFilePath)) {
    fs.writeFileSync(dataFilePath, '[]');
    console.log('sensor_data1.json 파일이 생성되었습니다.');
}

// 데이터 파일이 존재하면 기존 데이터를 로드
let sensorData = [];
try {
    const data = fs.readFileSync(dataFilePath, 'utf8');
    if (data.trim()) {
        sensorData = JSON.parse(data);
    }
    console.log('기존 데이터를 로드했습니다.');
} catch (err) {
    console.error('JSON 파싱 중 오류 발생:', err);
    sensorData = [];
}

// WebSocket 연결 처리
wss.on('connection', (ws) => {
    console.log('WebSocket 클라이언트 연결됨');
    clients.push(ws);

    // 연결된 클라이언트에게 누적된 데이터 전송
    ws.send(JSON.stringify(sensorData));

    ws.on('close', () => {
        clients = clients.filter(client => client !== ws);
        console.log('WebSocket 클라이언트 연결 종료');
    });
});

// 날짜 목록을 반환하는 API 엔드포인트
app.get('/api/dates', (req, res) => {
    const dates = [...new Set(sensorData.map(data => data.timestamp.split(' ')[0]))];
    res.json(dates);
});

// 선택한 날짜의 시간별 이벤트 카운트를 반환하는 API 엔드포인트
app.get('/api/sensor', (req, res) => {
    const { date } = req.query;
    if (!date) {
        return res.status(400).send('날짜가 지정되지 않았습니다.');
    }

    const hourlyCounts = Array(24).fill(0); // 0부터 23까지 각 시간별 카운트

    const filteredData = sensorData.filter(data => data.timestamp.startsWith(date));

    filteredData.forEach(data => {
        const time = data.timestamp.split(' ')[1];
        const hour = parseInt(time.split(':')[0], 10); // 시(hour)만 추출
        hourlyCounts[hour]++;
    });

    res.json({ hourlyCounts, filteredData });
});

// 라즈베리 파이로부터 데이터를 수신하는 API 엔드포인트
app.post('/api/sensor', (req, res) => {
    console.log('수신된 요청 본문:', req.body);
    try {
        const { event, value, timestamp, latitude, longitude, mac, ip } = req.body;
        if (!event || typeof value === 'undefined' || !timestamp) {
            throw new Error('필수 데이터 누락');
        }

        const newData = {
            event,
            value,
            timestamp,
            latitude: parseFloat(latitude) || null,
            longitude: parseFloat(longitude) || null,
            ip: ip || 'Unknown',  // 클라이언트에서 받은 ip를 사용
            mac: mac || 'Unknown'  // MAC 주소를 추가, 없으면 'Unknown'으로 설정
        };

        sensorData.push(newData);

        // 데이터를 파일에 저장
        fs.writeFile(dataFilePath, JSON.stringify(sensorData, null, 2), (err) => {
            if (err) {
                console.error('파일 저장 중 오류 발생:', err);
                return res.status(500).send('파일 저장 실패');
            }

            console.log('데이터가 파일에 저장되었습니다.');

            // 수신된 데이터를 모든 WebSocket 클라이언트에 전송
            clients.forEach(client => {
                if (client.readyState === WebSocket.OPEN) {
                    client.send(JSON.stringify(newData));
                }
            });

            res.status(200).send('데이터 수신 및 저장 완료');
        });
    } catch (err) {
        console.error('서버에서 JSON 처리 중 오류 발생:', err);
        res.status(400).send('잘못된 요청: ' + err.message);
    }
});

// HTML 파일을 서빙
app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'index.html'));
});

// 서버 시작
const PORT = process.env.PORT || 8080;
server.listen(PORT, '0.0.0.0', () => {
    console.log(`서버가 ${PORT}번 포트에서 실행 중입니다.`);
});
