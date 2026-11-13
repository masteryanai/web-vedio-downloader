"use strict";
import * as util from "./util.js";
export { NALU, splitNALU, joinNALU };
const NAL_START_FIRST = Uint8Array.of(0x00, 0x00, 0x00, 0x01);
const NAL_START_SECOND = Uint8Array.of(0x00, 0x00, 0x01);
function getNALUPos(buf) {
    let start, prev = 0, off = 0;
    const ret = [];
    buf.forEach((el, idx) => {
        // format:
        // !00 00 00 00 01 xx or
        // !00 00 00 01 xx
        if (el === 0 &&
            buf[idx + 1] === 0 &&
            (idx !== 0 && buf[idx - 1] !== 0) &&
            ((buf[idx + 2] === 0 && buf[idx + 3] === 1) || buf[idx + 2] === 1)) {
            ret.push([prev, idx]);
            prev = idx;
        }
    });
    ret.push([prev, buf.byteLength]);
    return ret;
}
function splitNALU(buf) {
    return getNALUPos(buf).map(([start, end]) => new NALU(buf.subarray(start, end)));
}
function joinNALU(nalus) {
    return util.concatUint8Arrays(nalus.map((e, i) => e.dump()));
}
class NALU {
    start;
    header;
    payload;
    forbiddenZeroBit;
    nalRefIdc;
    nalUnitType;
    constructor(data) {
        if (data.length <= 4)
            throw new Error("data length <= 4");
        if (util.arrayEquals(data.subarray(0, 4), NAL_START_FIRST)) {
            this.start = data.subarray(0, 4);
            this.header = data[4];
            this.payload = data.subarray(5);
        }
        else if (util.arrayEquals(data.subarray(0, 3), NAL_START_SECOND)) {
            this.start = data.subarray(0, 3);
            this.header = data[3];
            this.payload = data.subarray(4);
        }
        else
            throw new Error("NAL unit start mismatch");
        this.forbiddenZeroBit = this.header >> 7;
        this.nalRefIdc = this.header >> 5 & 0x3;
        this.nalUnitType = this.header & 0x1F;
    }
    reloadData(newData) {
        if (this.header !== newData[0])
            throw new Error("header changed");
        this.header = newData[0];
        this.payload = newData.subarray(1);
        this.forbiddenZeroBit = this.header >> 7;
        this.nalRefIdc = this.header >> 5 & 0x3;
        this.nalUnitType = this.header & 0x1F;
    }
    dump() {
        return Uint8Array.from([...this.start, this.header, ...this.payload]);
    }
}
