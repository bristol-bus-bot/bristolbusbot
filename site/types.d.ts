// Fields consumed from /api/buses (services/buses.py and map_timing.py).
type LiveBus = {
  operatorRef: string;
  line: string | null;
  destination: string;
  delayMinutes: number | null;
  eventType: BusStatus;
  waitingAtOrigin: boolean;
  directionId: number;
  directionRef: string;
  tripId: string | null;
  hasSchedule: boolean;
  timingSource: "collector" | "unavailable" | "scheduled_wait" | "route_estimate";
  fleetNumber: string | null;
  reg: string | null;
};

type BusStatus = "punctual" | "early" | "delayed" | "waiting" | "depot" | "unknown";

// /api/route-shapes (routes/api_misc.py), grouped by operator and route in the browser.
type RouteShape = {
  route: string;
  operator: string;
  direction: number;
  variant: number;
  points: [number, number][];
};

// Search fields mapped by /api/fleet (routes/api_misc.py).
type FleetSearchRecord = {
  fleet_number: string | number | null;
  fleet_code: string;
  reg: string;
  previous_reg: string;
  model: string;
  name: string;
};

type ElementChild = Node | string | number | false | null | undefined;

type AttributeKey = "class" | "id" | "role" | "title" | "tabindex"
  | "href" | "target" | "rel" | "src" | "alt" | "width" | "height"
  | "type" | "value" | "placeholder" | "name" | "for" | "datetime"
  | `data-${string}` | `aria-${string}`;
type ListenerKey = "onClick" | "onChange" | "onInput" | "onSubmit" | "onKeyDown";
type OmittedAttribute = null | undefined | false;
type ElementAttributes =
  Partial<Record<AttributeKey, string | number | boolean | null>>
  & Partial<Record<ListenerKey, EventListener | OmittedAttribute>>;

interface ObjectConstructor {
  // Preserve the attribute/listener distinction in the DOM helper's entries loop.
  entries(attributes: ElementAttributes): (
    [AttributeKey, string | number | boolean | null | undefined]
    | [ListenerKey, EventListener | OmittedAttribute]
  )[];
}

interface Window {
  BBB?: Record<string, unknown>;
}
