// bngApi.engineLua wraps callback requests as guihooks.trigger(..., EXPRESSION).
// A statement chunk (`local ...; return ...`) is invalid in that argument slot.
export function luaCall(expression) {
  return `(function() local b = beamngEarth_core or beamngEarth; return b and b.socialState and ${expression} or nil end)()`
}
