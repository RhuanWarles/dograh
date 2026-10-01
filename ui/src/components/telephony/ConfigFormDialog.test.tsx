import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfigFormDialog } from "./ConfigFormDialog";

const mocks = vi.hoisted(() => ({
  metadata: vi.fn(), create: vi.fn(), update: vi.fn(),
  auth: { user: { id: 1 }, loading: false, getAccessToken: vi.fn().mockResolvedValue("token") },
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => mocks.auth }));
vi.mock("@/client/sdk.gen", () => ({
  getTelephonyProvidersMetadataApiV1OrganizationsTelephonyProvidersMetadataGet: mocks.metadata,
  createTelephonyConfigurationApiV1OrganizationsTelephonyConfigsPost: mocks.create,
  updateTelephonyConfigurationApiV1OrganizationsTelephonyConfigsConfigIdPut: mocks.update,
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
const condition = { field: "connection_mode", equals: "sip_trunk" };
const field = (name: string, extra = {}) => ({ name, label: name, type: "text", required: true, sensitive: false, ...extra });
const provider = {
  provider: "ari", display_name: "SIP Trunk / Asterisk", connectivity: "sip",
  fields: [
    field("connection_mode", { type: "select", default_value: "external_ari", options: [{ value: "sip_trunk", label: "SIP" }, { value: "external_ari", label: "ARI" }] }),
    field("ari_endpoint", { visible_when: { ...condition, equals: "external_ari" } }),
    field("sip.host", { visible_when: condition }),
    field("sip.username", { visible_when: condition }),
    field("sip.password", { type: "password", sensitive: true, visible_when: condition }),
    field("sip.port", { type: "number", default_value: 5060, visible_when: condition }),
    field("sip.registration_enabled", { type: "boolean", default_value: true, visible_when: condition }),
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.auth.loading = false;
  mocks.metadata.mockResolvedValue({ data: { providers: [provider] } });
  mocks.create.mockResolvedValue({ data: { id: 123, provider: "ari" } });
});

describe("SIP configuration form", () => {
  it("submits nested carrier settings with defaults, without hidden ARI credentials", async () => {
    const saved = vi.fn();
    render(<ConfigFormDialog open onOpenChange={vi.fn()} initialProvider="ari" initialValues={{ connection_mode: "sip_trunk" }} onSaved={saved} />);
    await screen.findByLabelText("sip.host");
    expect(screen.queryByLabelText("ari_endpoint")).toBeNull();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Carrier A" } });
    for (const [label, value] of [["sip.host", "sip.example.com"], ["sip.username", "account"], ["sip.password", "secret"]]) {
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    }
    fireEvent.click(screen.getByText("Create", { selector: "button" }));
    await waitFor(() => expect(mocks.create).toHaveBeenCalled());
    expect(mocks.create.mock.calls[0][0].body.config).toEqual({
      provider: "ari", connection_mode: "sip_trunk",
      sip: { host: "sip.example.com", username: "account", password: "secret", port: 5060, registration_enabled: true },
    });
    expect(saved).toHaveBeenCalledWith({ id: 123, provider: "ari" });
  });

  it("waits for authentication before requesting provider metadata", async () => {
    mocks.auth.loading = true;
    render(<ConfigFormDialog open onOpenChange={vi.fn()} onSaved={vi.fn()} />);
    expect(mocks.metadata).not.toHaveBeenCalled();
  });
});
