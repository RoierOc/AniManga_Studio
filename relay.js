'use strict';
const net = require('net');
const server = net.createServer(src => {
    const dst = net.createConnection({ host: '127.0.0.1', port: 5101 });
    src.pipe(dst); dst.pipe(src);
    dst.on('error', () => src.destroy());
    src.on('error', () => dst.destroy());
});
server.listen(5100, '0.0.0.0');
