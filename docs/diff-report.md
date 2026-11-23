## apikit compatibility report

`1.4.0` -> `1.5.0` | operations 6 -> 5 | **suggested bump: major**

| severity | rule | location | finding | fix |
| --- | --- | --- | --- | --- |
| breaking | AP024 | `components/securitySchemes/OAuth2` | security scheme 'OAuth2' was removed | keep the scheme registered until every operation stops referencing it |
| breaking | AP001 | `paths/~1store~1inventory` | path /store/inventory was removed | restore /store/inventory, or ship the removal in a new major version |
| breaking | AP003 | `paths/~1pets~1{petId}/delete` | DELETE /pets/{petId} was removed | keep the operation, or remove it only in a major release |
| breaking | AP022 | `paths/~1pets/get/parameters/0/required` | parameter 'limit' (in: query) became required | give it a default, or bump the major version |
| breaking | AP016 | `paths/~1pets/get/parameters/1/schema/enum` | enum values removed: 'bird' | clients still send these values; keep them accepted or bump the major version |
| breaking | AP014 | `paths/~1pets/get/responses/200/content/application~1json/schema/items/properties/id/type` | type changed from 'integer' to 'string' | accept both integer and string during a deprecation window |
| breaking | AP026 | `paths/~1pets/get/responses/200/content/application~1json/schema/items/properties/name/maxLength` | maxLength lowered: 60 -> 40 | relax the bound back |
| breaking | AP011 | `paths/~1pets/get/responses/200/content/application~1json/schema/items/properties/status` | property 'status' was removed from a response | keep returning/sending it until the next major version |
| breaking | AP005 | `paths/~1pets/get/responses/400` | response 400 was removed | keep 400 in the contract, or bump the major version |
| breaking | AP010 | `paths/~1pets/post/requestBody/content/application~1json/schema/properties/species` | new required property 'species' in a request body | make it optional with a default, or bump the major version |
| warning | AP029 | `paths/~1pets/get/responses/200/content/application~1json/schema/items/properties/id/format` | format changed from 'int64' to 'uuid' | code generators map formats to different types; verify the clients |
| warning | AP028 | `paths/~1store~1orders/post/requestBody/content/application~1json/schema/properties/quantity/default` | default changed from 1 to 2 | clients relying on the default will silently change behaviour |
| info | AP002 | `paths/~1pets~1{petId}~1photos` | path /pets/{petId}/photos was added | additive change: clients can adopt it whenever convenient |
| info | AP041 | `paths/~1pets/get/parameters/2` | new optional parameter 'sort' (in: query) | additive change: document it in the changelog |
| info | AP037 | `paths/~1pets/get/responses/200/content/application~1json/schema/items/properties/createdAt` | property 'createdAt' was added | additive change: document it in the changelog |
| info | AP006 | `paths/~1pets/get/responses/429` | response 429 was added | additive change: make sure clients handle it |

**Verdict:** 10 breaking, 2 warnings, 4 info. Suggested version: `2.0.0`.
