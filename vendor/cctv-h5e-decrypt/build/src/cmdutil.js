export { LogType, setLogFunc, getLogFunc, setNoLog, getNoLog, debug, log, warn, error };
var LogType;
(function (LogType) {
    LogType[LogType["DEBUG"] = 0] = "DEBUG";
    LogType[LogType["LOG"] = 1] = "LOG";
    LogType[LogType["WARN"] = 2] = "WARN";
    LogType[LogType["ERROR"] = 3] = "ERROR";
})(LogType || (LogType = {}));
;
let logFunc = (type, content) => {
    switch (type) {
        case LogType.DEBUG:
            console.debug(content);
            break;
        case LogType.LOG:
            console.log(content);
            break;
        case LogType.WARN:
            console.warn(content);
            break;
        case LogType.ERROR:
            console.error(content);
            break;
    }
};
let noLog = false;
function setLogFunc(f) {
    logFunc = f;
}
function getLogFunc() {
    return logFunc;
}
function setNoLog(v) {
    noLog = v;
}
function getNoLog() {
    return noLog;
}
function debug(content) {
    if (noLog)
        return;
    logFunc(LogType.DEBUG, content);
}
function log(content) {
    if (noLog)
        return;
    logFunc(LogType.LOG, content);
}
function warn(content) {
    if (noLog)
        return;
    logFunc(LogType.WARN, content);
}
function error(content) {
    if (noLog)
        return;
    logFunc(LogType.ERROR, content);
}
