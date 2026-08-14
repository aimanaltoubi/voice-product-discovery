Examples:

Request: "Recommend an eco-friendly stainless-steel cleaner under fifteen dollars."
Output: {"task": "product_recommendation", "constraints": {"budget": 15, "material": "stainless steel", "brand": null, "category": "cleaner", "eco_friendly": true}, "safety_flags": [], "needs_live": false}

Request: "What's the current price of Weiman glass cleaner, is it in stock?"
Output: {"task": "price_check", "constraints": {"budget": null, "material": "glass", "brand": "Weiman", "category": "cleaner", "eco_friendly": null}, "safety_flags": [], "needs_live": true}

Request: "Can I mix bleach and ammonia to make a stronger cleaner?"
Output: {"task": "unsafe_chemistry_question", "constraints": {"budget": null, "material": null, "brand": null, "category": null, "eco_friendly": null}, "safety_flags": ["unsafe_chemical_mixing"], "needs_live": false}
