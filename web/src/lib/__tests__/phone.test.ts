import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { aeSaveError, normalisePhone, phoneHint, validateAe } from "../phone";

describe("AE form validation", () => {
  it("normalises spaced and 00-prefixed numbers to E.164", () => {
    expect(normalisePhone("+91 97899 98350")).toBe("+919789998350");
    expect(normalisePhone("0044 (20) 7946-0958")).toBe("+442079460958");
  });

  it("shows the normalised number as a hint", () => {
    expect(phoneHint("+91 97899 98350")).toEqual({ ok: true, text: "Saved as +919789998350" });
  });

  it("blocks numbers without a country code", () => {
    expect(phoneHint("97899 98350").ok).toBe(false);
  });

  it("blocks letters and too-short numbers", () => {
    expect(phoneHint("+91 9789x").ok).toBe(false);
    expect(phoneHint("+12345").ok).toBe(false);
  });

  it("requires email, name and phone on create", () => {
    const e = validateAe({ email: "", name: " ", phone: "", active: true });
    expect(Object.keys(e).sort()).toEqual(["email", "name", "phone"]);
  });

  it("rejects an invalid email", () => {
    expect(
      validateAe({ email: "priya@", name: "Priya", phone: "+919789998350", active: true }).email,
    ).toBeTruthy();
  });

  it("passes a valid AE", () => {
    expect(
      validateAe({ email: "priya@novacrm.com", name: "Priya", phone: "+91 97899 98350", active: true }),
    ).toEqual({});
  });

  it("lets edit keep the phone by leaving it empty, but still checks a typed one", () => {
    const o = { requireEmail: false, requirePhone: false };
    expect(validateAe({ email: "", name: "Priya", phone: "", active: true }, o)).toEqual({});
    expect(validateAe({ email: "", name: "Priya", phone: "123", active: true }, o).phone).toBeTruthy();
  });

  it("maps 409 to the email field and 422 to the named field", () => {
    expect(aeSaveError(new ApiError("dup", 409))).toEqual({
      field: "email",
      message: "An AE with this email already exists.",
    });
    expect(aeSaveError(new ApiError("phone: must be E.164", 422)).field).toBe("phone");
    expect(aeSaveError(new ApiError("server down", 500)).field).toBeUndefined();
  });
});
