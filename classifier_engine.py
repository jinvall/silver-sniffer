class Classifier:

    def classify(self, metrics):
        rate = metrics["rate"]
        std = metrics["rssi_std"]
        ent = metrics["channel_entropy"]
        burst = metrics["burstiness"]

        score_phone = (
            rate*0.4 +
            ent*2 +
            burst*1.5
        )

        score_iot = (
            (1/(rate+0.1))*2 +
            (1/(ent+0.1))*2 +
            (1/(std+0.1))
        )

        score_ap = (
            rate*1.2 +
            (1/(burst+0.1))*2
        )

        scores={
            "phone":score_phone,
            "iot":score_iot,
            "access_point":score_ap
        }

        cls=max(scores,key=scores.get)

        return {
            "class":cls,
            "scores":scores
        }

