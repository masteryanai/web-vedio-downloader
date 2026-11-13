export { WorkerMessageType, isNode };
// polyfills for node and browser
const isNode = typeof process === "object" && typeof process.versions === "object" && typeof process.versions.node === "string";
var WorkerMessageType;
(function (WorkerMessageType) {
    WorkerMessageType[WorkerMessageType["WANT_DECRYPT"] = 0] = "WANT_DECRYPT";
    WorkerMessageType[WorkerMessageType["CAN_PUSH_ENCRYPTED_BUFFER"] = 1] = "CAN_PUSH_ENCRYPTED_BUFFER";
    WorkerMessageType[WorkerMessageType["PUSH_WORKER_ENCRYPTED_BUFFER"] = 2] = "PUSH_WORKER_ENCRYPTED_BUFFER";
    WorkerMessageType[WorkerMessageType["PUSH_MAIN_THREAD_DECRYPTED_BUFFER"] = 3] = "PUSH_MAIN_THREAD_DECRYPTED_BUFFER";
    WorkerMessageType[WorkerMessageType["FINISH_DECRYPT"] = 4] = "FINISH_DECRYPT";
    WorkerMessageType[WorkerMessageType["FINISH_DESTROYING"] = 5] = "FINISH_DESTROYING";
    WorkerMessageType[WorkerMessageType["INIT_ERROR"] = 6] = "INIT_ERROR";
    WorkerMessageType[WorkerMessageType["DECRYPT_ERROR"] = 7] = "DECRYPT_ERROR";
})(WorkerMessageType || (WorkerMessageType = {}));
;
